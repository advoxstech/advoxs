"""Cobrança do cliente final: cada tenant usa a própria conta Stripe pra
vender créditos aos próprios clientes. Espelha app/services/billing.py
(billing tenant->plataforma), mas com a secret key sendo a do TENANT, nunca
a global — por isso toda chamada à Stripe aqui passa api_key= explicitamente,
nunca via stripe.api_key global (que vazaria entre tenants concorrentes).
"""

import asyncio
import logging
import uuid

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_tenant_secret
from app.models import EndCustomerCreditPackage, TenantBillingSettings

logger = logging.getLogger(__name__)


class BillingNotConfiguredError(Exception):
    """Tenant sem cobrança habilitada ou sem secret key configurada."""


class InvalidPackageError(Exception):
    """Pacote inexistente, de outro tenant, ou inativo."""


class StripeApiError(Exception):
    """Falha ao criar a sessão de checkout na Stripe (rede ou resposta de erro)."""


async def create_end_customer_checkout_session(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    contact_phone_number: str,
    package_id: uuid.UUID,
) -> str:
    billing_settings = await session.scalar(
        select(TenantBillingSettings).where(TenantBillingSettings.tenant_id == tenant_id)
    )
    if (
        billing_settings is None
        or not billing_settings.enabled
        or billing_settings.stripe_secret_key_encrypted is None
    ):
        raise BillingNotConfiguredError("Cobrança do cliente final não configurada pelo tenant")

    package = await session.scalar(
        select(EndCustomerCreditPackage).where(
            EndCustomerCreditPackage.id == package_id,
            EndCustomerCreditPackage.tenant_id == tenant_id,
        )
    )
    if package is None or not package.active:
        raise InvalidPackageError("Pacote de créditos inválido")

    secret_key = decrypt_tenant_secret(billing_settings.stripe_secret_key_encrypted)

    try:
        checkout_session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            api_key=secret_key,
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": "brl",
                        "unit_amount": int(package.price_brl * 100),
                        "product_data": {"name": package.name},
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "tenant_id": str(tenant_id),
                "contact_phone_number": contact_phone_number,
                "package_id": str(package_id),
                "kind": "end_customer_purchase",
            },
            success_url=f"{settings.web_app_url}/pagamento-confirmado",
            cancel_url=f"{settings.web_app_url}/pagamento-confirmado",
        )
    except stripe.error.StripeError as exc:
        logger.error("Falha ao criar checkout do cliente final | erro=%s", exc)
        raise StripeApiError("Falha ao iniciar o pagamento — tente novamente em instantes") from exc

    return checkout_session.url
