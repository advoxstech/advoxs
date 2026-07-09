from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.schemas.dashboard import TenantDashboardOut
from app.services.dashboard import build_tenant_dashboard

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
async def get_dashboard(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> TenantDashboardOut:
    return await build_tenant_dashboard(session, ctx.tenant_id)
