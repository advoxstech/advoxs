"""Cadastro self-service: cria a sessão de checkout e informa quando o tenant fica pronto."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_system_session
from app.core.redis import get_redis
from app.models import CreditTransaction
from app.schemas.signup import CheckoutUrlOut, SignupCheckoutRequest, SignupStatusOut
from app.services.billing import (
    EmailAlreadyExistsError,
    InvalidPackageError,
    StripeApiError,
    create_checkout_session,
)
from app.services.signup_tokens import claim_handoff_token

logger = logging.getLogger(__name__)

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
    if found is None:
        return SignupStatusOut(ready=False)

    # Entrega única (GETDEL): o primeiro polling após a conta ficar pronta
    # leva o token; chamadas seguintes (ou URL vazada depois) recebem null.
    login_token: str | None = None
    try:
        redis = await get_redis()
        login_token = await claim_handoff_token(redis, session_id)
    except Exception:
        logger.warning("Falha ao buscar token de auto-login | session=%s", session_id)
    return SignupStatusOut(ready=True, login_token=login_token)
