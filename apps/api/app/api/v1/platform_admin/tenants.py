import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PlatformAdminContext, get_current_platform_admin
from app.core.db import get_session
from app.schemas.admin_tenants import TenantDetailOut, TenantListItemOut
from app.services.admin_tenants import get_tenant_detail, list_tenants

router = APIRouter(prefix="/platform-admin/tenants", tags=["platform-admin"])


@router.get("")
async def list_tenants_route(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: PlatformAdminContext = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> list[TenantListItemOut]:
    return await list_tenants(session, limit, offset)


@router.get("/{tenant_id}")
async def get_tenant_route(
    tenant_id: uuid.UUID,
    admin: PlatformAdminContext = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> TenantDetailOut:
    detail = await get_tenant_detail(session, tenant_id, admin.admin_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant não encontrado")
    return detail
