"""Tutorial de primeira abertura — flag por tenant, mostrado uma única vez."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.models import Tenant
from app.schemas.onboarding import OnboardingOut

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("")
async def get_onboarding(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> OnboardingOut:
    tenant = await session.get(Tenant, ctx.tenant_id)
    return OnboardingOut(completed=tenant.onboarding_completed_at is not None)


@router.post("/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_onboarding(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Idempotente: qualquer saída do wizard completa; re-POST não altera."""
    tenant = await session.get(Tenant, ctx.tenant_id)
    if tenant.onboarding_completed_at is None:
        tenant.onboarding_completed_at = datetime.now(UTC)
        await session.commit()
