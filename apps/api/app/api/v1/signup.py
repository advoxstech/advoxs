"""Cadastro self-service: cria a sessão de checkout e informa quando o tenant fica pronto."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_system_session
from app.models import CreditTransaction
from app.schemas.signup import CheckoutUrlOut, SignupCheckoutRequest, SignupStatusOut
from app.services.billing import (
    EmailAlreadyExistsError,
    InvalidPackageError,
    StripeApiError,
    create_checkout_session,
)

router = APIRouter(prefix="/signup", tags=["signup"])


@router.post("/checkout")
async def checkout(
    body: SignupCheckoutRequest,
    session: AsyncSession = Depends(get_system_session),
) -> CheckoutUrlOut:
    try:
        checkout_url = await create_checkout_session(
            session, body.tenant_name, body.email, body.password, body.credit_package_id
        )
    except EmailAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except InvalidPackageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except StripeApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return CheckoutUrlOut(checkout_url=checkout_url)


@router.get("/status")
async def signup_status(
    session_id: str = Query(...),
    session: AsyncSession = Depends(get_system_session),
) -> SignupStatusOut:
    found = await session.scalar(
        select(CreditTransaction.id).where(CreditTransaction.stripe_payment_id == session_id)
    )
    return SignupStatusOut(ready=found is not None)
