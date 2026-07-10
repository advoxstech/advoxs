from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.core.config import settings
from app.models import Tenant, User
from app.schemas.profile import ChangePasswordRequest, ProfileOut, ProfileUpdateRequest
from app.services.profile import InvalidCurrentPasswordError, change_password, update_tenant_name

router = APIRouter(prefix="/profile", tags=["profile"])

ALLOWED_LOGO_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


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


@router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ProfileOut:
    filename = file.filename or ""
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_LOGO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato não suportado — envie PNG ou JPG",
        )

    data = await file.read()
    if len(data) > settings.logo_max_file_size_bytes:
        limite_mb = settings.logo_max_file_size_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Arquivo maior que {limite_mb} MB",
        )

    upload_dir = Path(settings.logo_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{ctx.tenant_id}{extension}"
    (upload_dir / stored_filename).write_bytes(data)

    tenant = await session.get(Tenant, ctx.tenant_id)
    tenant.logo_filename = stored_filename
    await session.commit()

    user = await session.get(User, ctx.user_id)
    return ProfileOut(
        tenant_name=tenant.name,
        email_contato=tenant.email_contato,
        has_logo=True,
        user_name=getattr(user, "name", ""),
        user_email=getattr(user, "email", ""),
    )


@router.get("/logo")
async def get_logo(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> Response:
    tenant = await session.get(Tenant, ctx.tenant_id)
    if tenant.logo_filename is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sem logo cadastrada")

    path = Path(settings.logo_upload_dir) / tenant.logo_filename
    extension = path.suffix.lower()
    content_type = ALLOWED_LOGO_EXTENSIONS.get(extension, "application/octet-stream")
    return Response(content=path.read_bytes(), media_type=content_type)
