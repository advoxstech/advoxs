from fastapi import APIRouter, Depends, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_system_session
from app.core.redis import get_redis
from app.schemas.platform_admin import (
    PlatformAdminLoginRequest,
    PlatformRefreshRequest,
    PlatformTokenPair,
)
from app.services import platform_admin_auth

router = APIRouter(prefix="/platform-admin/auth", tags=["platform-admin"])


@router.post("/login")
async def login(
    body: PlatformAdminLoginRequest,
    session: AsyncSession = Depends(get_system_session),
) -> PlatformTokenPair:
    access_token, refresh_token = await platform_admin_auth.login(
        body.email, body.password, session
    )
    return PlatformTokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh")
async def refresh(
    body: PlatformRefreshRequest,
    session: AsyncSession = Depends(get_system_session),
    redis: Redis = Depends(get_redis),
) -> PlatformTokenPair:
    access_token, refresh_token = await platform_admin_auth.refresh(
        body.refresh_token, session, redis
    )
    return PlatformTokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: PlatformRefreshRequest,
    redis: Redis = Depends(get_redis),
) -> None:
    await platform_admin_auth.logout(body.refresh_token, redis)
