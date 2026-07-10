from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PlatformAdminContext, get_current_platform_admin
from app.core.db import get_system_session
from app.schemas.admin_dashboard import AdminDashboardOut
from app.services.admin_dashboard import build_dashboard

router = APIRouter(prefix="/platform-admin/dashboard", tags=["platform-admin"])


@router.get("")
async def get_dashboard(
    admin: PlatformAdminContext = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_system_session),
) -> AdminDashboardOut:
    return await build_dashboard(session)
