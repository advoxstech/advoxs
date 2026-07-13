"""Webhook da Stripe de cada tenant (cobrança do cliente final).

Cada tenant configura, no próprio Dashboard Stripe, um endpoint apontando
pra /webhooks/stripe/tenant/{tenant_id} — o tenant_id na URL é só roteamento
pra achar o webhook secret certo ANTES de validar a assinatura (não é
possível "tentar" o secret de todos os tenants contra um payload).
"""

import logging
import uuid

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_tenant_secret
from app.core.db import get_system_session
from app.models import TenantBillingSettings
from app.services.end_customer_billing import process_end_customer_checkout_completed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/stripe/tenant", tags=["webhooks"])

_ASSINATURA_INVALIDA = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST, detail="Assinatura inválida"
)


@router.post("/{tenant_id}")
async def receive_tenant_webhook(
    tenant_id: uuid.UUID,
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    session: AsyncSession = Depends(get_system_session),
) -> dict:
    billing_settings = await session.scalar(
        select(TenantBillingSettings).where(TenantBillingSettings.tenant_id == tenant_id)
    )
    if billing_settings is None or billing_settings.stripe_webhook_secret_encrypted is None:
        # Mesmo erro genérico de assinatura inválida — não revela se o
        # tenant existe ou não configurou o webhook.
        raise _ASSINATURA_INVALIDA

    webhook_secret = decrypt_tenant_secret(billing_settings.stripe_webhook_secret_encrypted)
    raw_body = await request.body()
    try:
        event = stripe.Webhook.construct_event(raw_body, stripe_signature, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        logger.warning(
            "Assinatura de webhook de tenant inválida | tenant=%s erro=%s", tenant_id, exc
        )
        raise _ASSINATURA_INVALIDA

    if event["type"] == "checkout.session.completed":
        await process_end_customer_checkout_completed(session, tenant_id, event["data"]["object"])

    return {"status": "ok"}
