"""Webhook da Stripe: confirmação de pagamento do cadastro self-service."""

import logging

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.services.billing import process_checkout_completed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/stripe", tags=["webhooks"])


@router.post("")
async def receive_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    raw_body = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            raw_body, stripe_signature, settings.stripe_webhook_secret
        )
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        logger.warning("Assinatura de webhook inválida | erro=%s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Assinatura inválida")

    if event["type"] == "checkout.session.completed":
        await process_checkout_completed(session, event["data"]["object"])

    return {"status": "ok"}
