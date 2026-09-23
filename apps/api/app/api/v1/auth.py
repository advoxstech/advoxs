from fastapi import APIRouter, Depends, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_system_session
from app.core.redis import get_redis
from app.schemas.auth import LoginRequest, RefreshRequest, SignupLoginRequest, TokenPair
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_system_session),
    redis: Redis = Depends(get_redis),
) -> TokenPair:
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",", maxsplit=1)[0].strip()
    client_ip = forwarded_for or (request.client.host if request.client else "unknown")
    access_token, refresh_token = await auth_service.login(
        body.email, body.password, session, redis, client_ip
    )
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/signup-login")
async def signup_login(
    body: SignupLoginRequest,
    session: AsyncSession = Depends(get_system_session),
    redis: Redis = Depends(get_redis),
) -> TokenPair:
    access_token, refresh_token = await auth_service.signup_token_login(body.token, session, redis)
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh")
async def refresh(
    body: RefreshRequest,
    session: AsyncSession = Depends(get_system_session),
    redis: Redis = Depends(get_redis),
) -> TokenPair:
    access_token, refresh_token = await auth_service.refresh(body.refresh_token, session, redis)
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: RefreshRequest,
    redis: Redis = Depends(get_redis),
) -> None:
    await auth_service.logout(body.refresh_token, redis)
