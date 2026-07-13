"""Cobrança do cliente final: cada tenant usa a própria conta Stripe pra
vender créditos aos próprios clientes. Espelha app/services/billing.py
(billing tenant->plataforma), mas com a secret key sendo a do TENANT, nunca
a global — por isso toda chamada à Stripe aqui passa api_key= explicitamente,
nunca via stripe.api_key global (que vazaria entre tenants concorrentes).
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.whatsapp import WhatsAppSendError, send_text_message
from app.core.config import settings
from app.core.crypto import decrypt_access_token, decrypt_tenant_secret
from app.models import (
    Conversation,
    EndCustomerBalance,
    EndCustomerCreditPackage,
    EndCustomerCreditTransaction,
    Message,
    TenantBillingSettings,
    WhatsAppNumber,
)

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


async def process_end_customer_checkout_completed(
    session: AsyncSession, tenant_id: uuid.UUID, stripe_session: dict
) -> None:
    """Credita o pacote comprado pelo cliente final e confirma via WhatsApp.

    Idempotente por stripe_payment_id, mesmo padrão do billing tenant->plataforma.
    """
    session_id = stripe_session["id"]
    already_processed = await session.scalar(
        select(EndCustomerCreditTransaction.id).where(
            EndCustomerCreditTransaction.stripe_payment_id == session_id
        )
    )
    if already_processed is not None:
        logger.info("Webhook de cliente final duplicado, ignorando | session=%s", session_id)
        return

    raw_metadata = stripe_session["metadata"] if "metadata" in stripe_session else {}
    metadata = raw_metadata.to_dict() if hasattr(raw_metadata, "to_dict") else dict(raw_metadata)

    if metadata.get("kind") != "end_customer_purchase":
        return

    contact_phone_number = metadata.get("contact_phone_number")
    package_id_raw = metadata.get("package_id")
    if not contact_phone_number or not package_id_raw:
        logger.error("Metadata incompleta no webhook de cliente final | session=%s", session_id)
        return

    package = await session.scalar(
        select(EndCustomerCreditPackage).where(
            EndCustomerCreditPackage.id == uuid.UUID(package_id_raw),
            EndCustomerCreditPackage.tenant_id == tenant_id,
        )
    )
    if package is None:
        logger.error("Pacote não encontrado no webhook de cliente final | session=%s", session_id)
        return

    balance = await session.scalar(
        select(EndCustomerBalance).where(
            EndCustomerBalance.tenant_id == tenant_id,
            EndCustomerBalance.contact_phone_number == contact_phone_number,
        )
    )
    if balance is None:
        balance = EndCustomerBalance(
            tenant_id=tenant_id, contact_phone_number=contact_phone_number, credit_balance=0
        )
        session.add(balance)
        await session.flush()

    balance.credit_balance += package.credits_granted
    balance.updated_at = datetime.now(UTC)

    session.add(
        EndCustomerCreditTransaction(
            tenant_id=tenant_id,
            contact_phone_number=contact_phone_number,
            type="purchase",
            amount_credits=package.credits_granted,
            end_customer_credit_package_id=package.id,
            stripe_payment_id=session_id,
            description=f"Compra do pacote {package.name}",
        )
    )
    await session.commit()

    await _send_purchase_confirmation(session, tenant_id, contact_phone_number)


async def _send_purchase_confirmation(
    session: AsyncSession, tenant_id: uuid.UUID, contact_phone_number: str
) -> None:
    """Best-effort: uma falha ao mandar a confirmação não desfaz o crédito
    já commitado acima — o cliente só não recebe o aviso, mas o saldo está lá."""
    try:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.contact_phone_number == contact_phone_number,
            )
        )
        number = await session.scalar(
            select(WhatsAppNumber).where(
                WhatsAppNumber.tenant_id == tenant_id, WhatsAppNumber.status == "connected"
            )
        )
        if number is None or conversation is None:
            logger.warning(
                "Sem número/conversa pra confirmar pagamento | tenant=%s contato=%s",
                tenant_id,
                contact_phone_number,
            )
            return

        await send_text_message(
            phone_number_id=number.phone_number_id,
            access_token=decrypt_access_token(number.access_token_encrypted),
            to=contact_phone_number,
            text="Pagamento confirmado! Você já pode continuar a conversa.",
        )

        session.add(
            Message(
                conversation_id=conversation.id,
                tenant_id=tenant_id,
                sender_type="system",
                content="Pagamento confirmado! Você já pode continuar a conversa.",
                delivery_status="sent",
            )
        )
        conversation.last_message_at = datetime.now(UTC)
        await session.commit()
    except WhatsAppSendError:
        logger.exception(
            "Falha ao confirmar pagamento via WhatsApp | tenant=%s contato=%s",
            tenant_id,
            contact_phone_number,
        )
    except Exception:
        logger.exception(
            "Erro inesperado ao confirmar pagamento | tenant=%s contato=%s",
            tenant_id,
            contact_phone_number,
        )
