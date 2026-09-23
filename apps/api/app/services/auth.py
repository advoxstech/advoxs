"""Login, refresh com rotação e logout (revogação via blacklist no Redis)."""

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime

import jwt
from fastapi import HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import Tenant, User
from app.services.signup_tokens import consume_login_token

logger = logging.getLogger(__name__)

BLACKLIST_PREFIX = "auth:blacklist:"
ROTATION_RESULT_PREFIX = "auth:rotation-result:"
LOGIN_ACCOUNT_PREFIX = "auth:login-failures:account:"
LOGIN_IP_PREFIX = "auth:login-failures:ip:"
ROTATION_GRACE_SECONDS = 5

# Hash de comparação para e-mail inexistente — iguala o tempo de resposta e
# evita enumeração de contas pelo timing.
_DUMMY_HASH = hash_password("dummy-timing-equalizer")

_CREDENCIAIS_INVALIDAS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas"
)

_TOKEN_INVALIDO = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido ou expirado"
)


async def login(
    email: str,
    password: str,
    session: AsyncSession,
    redis: Redis,
    client_ip: str,
) -> tuple[str, str]:
    account_key = _login_key(LOGIN_ACCOUNT_PREFIX, email.strip().lower())
    ip_key = _login_key(LOGIN_IP_PREFIX, client_ip)
    await _enforce_login_limit(redis, account_key, ip_key)

    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        await _record_login_failure(redis, account_key, ip_key)
        raise _CREDENCIAIS_INVALIDAS
    if not verify_password(password, user.password_hash):
        await _record_login_failure(redis, account_key, ip_key)
        raise _CREDENCIAIS_INVALIDAS

    await _validar_tenant_ativo(user, session)

    await _clear_account_failures(redis, account_key)
    logger.info("Login | user=%s tenant=%s", user.id, user.tenant_id)
    return (
        create_access_token(str(user.id), str(user.tenant_id), user.role, user.session_version),
        create_refresh_token(str(user.id), user.session_version),
    )


async def signup_token_login(token: str, session: AsyncSession, redis: Redis) -> tuple[str, str]:
    """Troca o token one-time do cadastro por um par de JWT (uso único).

    401 genérico pra token inválido/expirado/reusado e pra user inexistente —
    sem oráculo de qual caso ocorreu.
    """
    user_id = await consume_login_token(redis, token)
    if user_id is None:
        raise _TOKEN_INVALIDO

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        # Valor malformado no Redis = bug interno nosso; 401 genérico em vez
        # de 500 — mesma paridade do refresh() acima.
        raise _TOKEN_INVALIDO

    user = await session.get(User, user_uuid)
    if user is None:
        raise _TOKEN_INVALIDO

    await _validar_tenant_ativo(user, session)

    logger.info("Auto-login pós-cadastro | user=%s tenant=%s", user.id, user.tenant_id)
    return (
        create_access_token(str(user.id), str(user.tenant_id), user.role, user.session_version),
        create_refresh_token(str(user.id), user.session_version),
    )


async def refresh(refresh_token: str, session: AsyncSession, redis: Redis) -> tuple[str, str]:
    """Rotação: valida o refresh token, revoga o jti antigo e emite um novo par."""
    payload = _decode_refresh(refresh_token)

    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        raise _CREDENCIAIS_INVALIDAS
    user = await session.get(User, user_id)
    if user is None:
        raise _CREDENCIAIS_INVALIDAS
    if payload.get("session_version", 0) != user.session_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessão revogada")
    await _validar_tenant_ativo(user, session)

    result_key = f"{ROTATION_RESULT_PREFIX}{payload['jti']}"
    if not await _claim_refresh(redis, payload):
        concurrent_result = await _wait_for_rotation_result(redis, result_key)
        if concurrent_result is not None:
            return concurrent_result
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revogado"
        )

    tokens = (
        create_access_token(str(user.id), str(user.tenant_id), user.role, user.session_version),
        create_refresh_token(str(user.id), user.session_version),
    )
    try:
        await redis.set(result_key, json.dumps(tokens), ex=ROTATION_GRACE_SECONDS)
    except RedisError:
        logger.warning("Não foi possível publicar o resultado da rotação concorrente")
    return tokens


async def logout(refresh_token: str, redis: Redis) -> None:
    """Revoga o refresh token. Access tokens expiram sozinhos (vida curta)."""
    payload = _decode_refresh(refresh_token)
    await _blacklist(redis, payload)


def _decode_refresh(token: str) -> dict:
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token inválido"
        )
    if payload.get("type") != "refresh" or "jti" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token inválido"
        )
    return payload


async def _blacklist(redis: Redis, payload: dict) -> None:
    # TTL = tempo restante até a expiração — a chave some junto com o token.
    ttl = int(payload["exp"] - datetime.now(UTC).timestamp())
    if ttl > 0:
        await redis.set(f"{BLACKLIST_PREFIX}{payload['jti']}", "1", ex=ttl)


async def _claim_refresh(redis: Redis, payload: dict) -> bool:
    ttl = int(payload["exp"] - datetime.now(UTC).timestamp())
    if ttl <= 0:
        return False
    claimed = await redis.set(f"{BLACKLIST_PREFIX}{payload['jti']}", "1", ex=ttl, nx=True)
    return bool(claimed)


async def _wait_for_rotation_result(redis: Redis, key: str) -> tuple[str, str] | None:
    for _ in range(5):
        value = await redis.get(key)
        if value:
            access_token, refresh_token = json.loads(value)
            return access_token, refresh_token
        await asyncio.sleep(0.05)
    return None


def _login_key(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()
    return f"{prefix}{digest}"


async def _enforce_login_limit(redis: Redis, *keys: str) -> None:
    limits = (settings.login_max_attempts_per_account, settings.login_max_attempts_per_ip)
    try:
        for key, limit in zip(keys, limits, strict=True):
            attempts = int(await redis.get(key) or 0)
            if attempts >= limit:
                retry_after = max(await redis.ttl(key), 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Muitas tentativas. Tente novamente em alguns minutos.",
                    headers={"Retry-After": str(retry_after)},
                )
    except RedisError:
        logger.warning("Limitador de login indisponível; autenticação seguirá sem limite")


async def _record_login_failure(redis: Redis, *keys: str) -> None:
    try:
        for key in keys:
            attempts = await redis.incr(key)
            if attempts == 1:
                await redis.expire(key, settings.login_attempt_window_seconds)
    except RedisError:
        logger.warning("Não foi possível registrar falha no limitador de login")


async def _clear_account_failures(redis: Redis, account_key: str) -> None:
    try:
        await redis.delete(account_key)
    except RedisError:
        logger.warning("Não foi possível limpar o limitador da conta autenticada")


async def _validar_tenant_ativo(user: User, session: AsyncSession) -> None:
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None or tenant.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Escritório suspenso")
