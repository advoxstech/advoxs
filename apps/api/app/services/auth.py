"""Login, refresh com rotação e logout (revogação via blacklist no Redis)."""

import logging
import uuid
from datetime import UTC, datetime

import jwt
from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

# Hash de comparação para e-mail inexistente — iguala o tempo de resposta e
# evita enumeração de contas pelo timing.
_DUMMY_HASH = hash_password("dummy-timing-equalizer")

_CREDENCIAIS_INVALIDAS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas"
)

_TOKEN_INVALIDO = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido ou expirado"
)


async def login(email: str, password: str, session: AsyncSession) -> tuple[str, str]:
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        raise _CREDENCIAIS_INVALIDAS
    if not verify_password(password, user.password_hash):
        raise _CREDENCIAIS_INVALIDAS

    await _validar_tenant_ativo(user, session)

    logger.info("Login | user=%s tenant=%s", user.id, user.tenant_id)
    return (
        create_access_token(str(user.id), str(user.tenant_id), user.role),
        create_refresh_token(str(user.id)),
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
        create_access_token(str(user.id), str(user.tenant_id), user.role),
        create_refresh_token(str(user.id)),
    )


async def refresh(refresh_token: str, session: AsyncSession, redis: Redis) -> tuple[str, str]:
    """Rotação: valida o refresh token, revoga o jti antigo e emite um novo par."""
    payload = _decode_refresh(refresh_token)

    if await redis.exists(f"{BLACKLIST_PREFIX}{payload['jti']}"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revogado"
        )

    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        raise _CREDENCIAIS_INVALIDAS
    user = await session.get(User, user_id)
    if user is None:
        raise _CREDENCIAIS_INVALIDAS
    await _validar_tenant_ativo(user, session)

    await _blacklist(redis, payload)

    return (
        create_access_token(str(user.id), str(user.tenant_id), user.role),
        create_refresh_token(str(user.id)),
    )


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


async def _validar_tenant_ativo(user: User, session: AsyncSession) -> None:
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None or tenant.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Escritório suspenso")
