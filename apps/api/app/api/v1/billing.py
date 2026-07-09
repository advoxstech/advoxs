"""Recompra de créditos pelo tenant autenticado: saldo, checkout e status."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.core.db import get_session
from app.models import CreditTransaction, Tenant
from app.schemas.billing import (
    BillingBalanceOut,
    BillingCheckoutRequest,
    BillingCheckoutUrlOut,
    BillingStatusOut,
)
from app.services.billing import (
    InvalidPackageError,
    StripeApiError,
    create_recompra_checkout_session,
)

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/balance")
async def get_balance(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> BillingBalanceOut:
    tenant = await session.get(Tenant, ctx.tenant_id)
    return BillingBalanceOut(credit_balance=tenant.credit_balance)


@router.post("/checkout")
async def checkout(
    body: BillingCheckoutRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> BillingCheckoutUrlOut:
    try:
        checkout_url = await create_recompra_checkout_session(
            session, ctx.tenant_id, body.credit_package_id
        )
    except InvalidPackageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except StripeApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return BillingCheckoutUrlOut(checkout_url=checkout_url)


@router.get("/status")
async def billing_status(
    session_id: str = Query(...),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> BillingStatusOut:
    found = await session.scalar(
        select(CreditTransaction.id).where(
            CreditTransaction.tenant_id == ctx.tenant_id,
            CreditTransaction.stripe_payment_id == session_id,
        )
    )
    return BillingStatusOut(ready=found is not None)
