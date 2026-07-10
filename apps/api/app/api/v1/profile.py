from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.models import Tenant, User
from app.schemas.profile import ChangePasswordRequest, ProfileOut, ProfileUpdateRequest
from app.services.profile import InvalidCurrentPasswordError, change_password, update_tenant_name

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("")
async def get_profile(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ProfileOut:
    tenant = await session.get(Tenant, ctx.tenant_id)
    user = await session.get(User, ctx.user_id)
    return ProfileOut(
        tenant_name=tenant.name,
        email_contato=tenant.email_contato,
        has_logo=tenant.logo_filename is not None,
        user_name=user.name,
        user_email=user.email,
    )


@router.patch("")
async def update_profile(
    body: ProfileUpdateRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ProfileOut:
    tenant = await update_tenant_name(session, ctx.tenant_id, body.tenant_name)
    user = await session.get(User, ctx.user_id)
    return ProfileOut(
        tenant_name=tenant.name,
        email_contato=tenant.email_contato,
        has_logo=tenant.logo_filename is not None,
        user_name=user.name,
        user_email=user.email,
    )


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password_route(
    body: ChangePasswordRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    try:
        await change_password(session, ctx.user_id, body.current_password, body.new_password)
    except InvalidCurrentPasswordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
