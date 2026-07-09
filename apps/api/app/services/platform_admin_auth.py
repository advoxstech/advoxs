"""Login, refresh com rotação e logout do platform_admin.

Isolado do auth de tenant: secret e prefixo de blacklist próprios — nunca
compartilha token nem estado com app.services.auth (login de tenant).
"""

import logging
import uuid
from datetime import UTC, datetime

import jwt
from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.platform_security import (
    create_platform_access_token,
    create_platform_refresh_token,
    decode_platform_token,
)
from app.core.security import hash_password, verify_password
from app.models import PlatformAdmin

logger = logging.getLogger(__name__)

BLACKLIST_PREFIX = "platform_auth:blacklist:"

# Hash de comparação para e-mail inexistente — iguala o tempo de resposta.
_DUMMY_HASH = hash_password("dummy-timing-equalizer")

_CREDENCIAIS_INVALIDAS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas"
)


async def login(email: str, password: str, session: AsyncSession) -> tuple[str, str]:
    admin = await session.scalar(select(PlatformAdmin).where(PlatformAdmin.email == email))
    if admin is None:
        verify_password(password, _DUMMY_HASH)
        raise _CREDENCIAIS_INVALIDAS
    if not verify_password(password, admin.password_hash):
        raise _CREDENCIAIS_INVALIDAS

    logger.info("Login de platform_admin | admin=%s", admin.id)
    return (
        create_platform_access_token(str(admin.id), admin.role),
        create_platform_refresh_token(str(admin.id)),
    )


async def refresh(refresh_token: str, session: AsyncSession, redis: Redis) -> tuple[str, str]:
    payload = _decode_refresh(refresh_token)

    if await redis.exists(f"{BLACKLIST_PREFIX}{payload['jti']}"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revogado"
        )

    try:
        admin_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        raise _CREDENCIAIS_INVALIDAS
    admin = await session.get(PlatformAdmin, admin_id)
    if admin is None:
        raise _CREDENCIAIS_INVALIDAS

    await _blacklist(redis, payload)

    return (
        create_platform_access_token(str(admin.id), admin.role),
        create_platform_refresh_token(str(admin.id)),
    )


async def logout(refresh_token: str, redis: Redis) -> None:
    payload = _decode_refresh(refresh_token)
    await _blacklist(redis, payload)


def _decode_refresh(token: str) -> dict:
    try:
        payload = decode_platform_token(token)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token inválido"
        )
    if payload.get("type") != "platform_refresh" or "jti" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token inválido"
        )
    return payload


async def _blacklist(redis: Redis, payload: dict) -> None:
    ttl = int(payload["exp"] - datetime.now(UTC).timestamp())
    if ttl > 0:
        await redis.set(f"{BLACKLIST_PREFIX}{payload['jti']}", "1", ex=ttl)
