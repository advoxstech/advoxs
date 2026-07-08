"""Checkout de créditos (Stripe) e provisionamento do tenant após pagamento.

Nada é persistido antes do pagamento confirmar: create_checkout_session só
valida e cria a sessão na Stripe, guardando os dados do cadastro na
metadata; process_checkout_completed (chamado pelo webhook) é quem de fato
cria tenant/user/credit_transaction.
"""

import asyncio
import logging
import uuid

import stripe
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.models import CreditPackage, CreditTransaction, Tenant, User

logger = logging.getLogger(__name__)

stripe.api_key = settings.stripe_secret_key


class EmailAlreadyExistsError(Exception):
    """E-mail já usado por outra conta — mapeado para 409 na rota."""


class InvalidPackageError(Exception):
    """Pacote de créditos inexistente ou inativo — mapeado para 400 na rota."""


class StripeApiError(Exception):
    """Falha ao criar a sessão de checkout na Stripe (rede ou resposta de erro)."""


async def create_checkout_session(
    session: AsyncSession,
    tenant_name: str,
    email: str,
    password: str,
    credit_package_id: uuid.UUID,
) -> str:
    existing = await session.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise EmailAlreadyExistsError(
            "Este e-mail já está cadastrado — faça login ou use outro e-mail"
        )

    package = await session.get(CreditPackage, credit_package_id)
    if package is None or not package.active:
        raise InvalidPackageError("Pacote de créditos inválido")

    try:
        checkout_session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "brl",
                        "unit_amount": int(package.price_brl * 100),
                        "product_data": {"name": f"Advoxs — {package.name}"},
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "tenant_name": tenant_name,
                "email": email,
                "password_hash": hash_password(password),
                "credit_package_id": str(credit_package_id),
            },
            success_url=f"{settings.web_app_url}/cadastro/sucesso?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{settings.web_app_url}/cadastro/cancelado",
        )
    except stripe.error.StripeError as exc:
        logger.error("Falha ao criar sessão de checkout | erro=%s", exc)
        raise StripeApiError("Falha ao iniciar o pagamento — tente novamente em instantes") from exc

    return checkout_session.url


async def process_checkout_completed(session: AsyncSession, stripe_session: dict) -> None:
    """Cria tenant+user+credit_transaction a partir da metadata da sessão paga.

    Idempotente: uma sessão já processada (mesmo id) não cria duplicata.
    """
    session_id = stripe_session["id"]
    already_processed = await session.scalar(
        select(CreditTransaction.id).where(CreditTransaction.stripe_payment_id == session_id)
    )
    if already_processed is not None:
        logger.info("Webhook duplicado, ignorando | session=%s", session_id)
        return

    metadata = stripe_session.get("metadata") or {}
    tenant_name = metadata.get("tenant_name")
    email = metadata.get("email")
    password_hash = metadata.get("password_hash")
    credit_package_id = metadata.get("credit_package_id")
    if not all([tenant_name, email, password_hash, credit_package_id]):
        logger.error("Metadata incompleta no checkout.session.completed | session=%s", session_id)
        return

    package = await session.get(CreditPackage, uuid.UUID(credit_package_id))
    if package is None:
        logger.error("Pacote não encontrado ao processar pagamento | session=%s", session_id)
        return

    tenant = Tenant(name=tenant_name, email_contato=email, credit_balance=package.credits_granted)
    session.add(tenant)
    await session.flush()

    user = User(
        tenant_id=tenant.id,
        name=tenant_name,
        email=email,
        password_hash=password_hash,
        role="admin",
    )
    session.add(user)

    session.add(
        CreditTransaction(
            tenant_id=tenant.id,
            type="purchase",
            amount_credits=package.credits_granted,
            credit_package_id=package.id,
            stripe_payment_id=session_id,
            description=f"Compra do pacote {package.name}",
        )
    )

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.critical(
            "Pagamento aprovado mas não foi possível provisionar o tenant "
            "(e-mail já existe?) | session=%s email=%s",
            session_id,
            email,
        )
