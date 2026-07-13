"""Endpoint interno chamado pelo agents (nunca pelo escritório/cliente
final diretamente) — cria o Checkout Session sem expor a secret key do
tenant ao serviço de agentes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.internal_deps import verify_internal_service_key
from app.core.db import get_system_session
from app.schemas.end_customer_billing import CheckoutUrlOut, InternalCheckoutRequest
from app.services.end_customer_billing import (
    BillingNotConfiguredError,
    InvalidPackageError,
    StripeApiError,
    create_end_customer_checkout_session,
)

router = APIRouter(
    prefix="/internal/end-customer-billing",
    tags=["internal"],
    dependencies=[Depends(verify_internal_service_key)],
)


@router.post("/checkout")
async def create_checkout(
    body: InternalCheckoutRequest,
    session: AsyncSession = Depends(get_system_session),
) -> CheckoutUrlOut:
    try:
        checkout_url = await create_end_customer_checkout_session(
            session, body.tenant_id, body.contact_phone_number, body.package_id
        )
    except BillingNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidPackageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except StripeApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return CheckoutUrlOut(checkout_url=checkout_url)
