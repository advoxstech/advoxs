"""Cadastro self-service: cria a sessão de checkout e informa quando o tenant fica pronto."""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_system_session
from app.core.redis import get_redis
from app.models import CreditTransaction
from app.schemas.signup import (
    CheckoutUrlOut,
    ResendVerificationRequest,
    SignupCheckoutRequest,
    SignupStatusOut,
    VerifiedCheckoutRequest,
    VerifyEmailOut,
    VerifyEmailRequest,
)
from app.services.billing import (
    EmailAlreadyExistsError,
    InvalidPackageError,
    StripeApiError,
    create_verified_checkout_session,
)
from app.services.signup_tokens import claim_handoff_token
from app.services.signup_verification import (
    get_verified_signup,
    request_verification,
    resend_verification,
    verify_email,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/signup", tags=["signup"])


@router.post("/request-verification", status_code=status.HTTP_202_ACCEPTED)
async def request_signup_verification(
    body: SignupCheckoutRequest,
    session: AsyncSession = Depends(get_system_session),
) -> dict[str, str]:
    await request_verification(session, body)
    return {"status": "sent"}


@router.post("/verify-email")
async def confirm_signup_email(
    body: VerifyEmailRequest,
    session: AsyncSession = Depends(get_system_session),
) -> VerifyEmailOut:
    return VerifyEmailOut(checkout_token=await verify_email(session, body.token))


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
async def resend_signup_verification(
    body: ResendVerificationRequest,
    session: AsyncSession = Depends(get_system_session),
) -> dict[str, str]:
    await resend_verification(session, str(body.email))
    return {"status": "sent"}


@router.post("/checkout")
async def checkout(
    body: VerifiedCheckoutRequest,
    session: AsyncSession = Depends(get_system_session),
) -> CheckoutUrlOut:
    pending = await get_verified_signup(session, body.checkout_token)
    now = datetime.now(UTC)
    if (
        pending.stripe_checkout_url
        and pending.stripe_checkout_created_at
        and now - pending.stripe_checkout_created_at < timedelta(hours=24)
    ):
        return CheckoutUrlOut(checkout_url=pending.stripe_checkout_url)
    try:
        checkout_id, checkout_url = await create_verified_checkout_session(session, pending)
    except EmailAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except InvalidPackageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except StripeApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    pending.stripe_checkout_id = checkout_id
    pending.stripe_checkout_url = checkout_url
    pending.stripe_checkout_created_at = now
    await session.commit()
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
