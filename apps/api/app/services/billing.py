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
from app.core.redis import get_redis
from app.core.security import hash_password
from app.models import CreditPackage, CreditTransaction, Tenant, User
from app.services.default_agents import build_default_agents
from app.services.default_subscription import build_default_subscription
from app.services.signup_tokens import store_login_token

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


async def create_recompra_checkout_session(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    credit_package_id: uuid.UUID,
) -> str:
    """Checkout de recompra — tenant já existe e está autenticado; o
    tenant_id vem sempre do contexto autenticado (nunca do corpo da
    requisição do cliente) e é gravado na metadata pelo servidor."""
    package = await session.get(CreditPackage, credit_package_id)
    if package is None or not package.active:
        raise InvalidPackageError("Pacote de créditos inválido")

    try:
        checkout_session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            mode="payment",
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
                "flow": "recompra",
                "tenant_id": str(tenant_id),
                "credit_package_id": str(credit_package_id),
            },
            success_url=f"{settings.web_app_url}/creditos/sucesso?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{settings.web_app_url}/creditos",
        )
    except stripe.error.StripeError as exc:
        logger.error("Falha ao criar sessão de recompra | erro=%s", exc)
        raise StripeApiError("Falha ao iniciar o pagamento — tente novamente em instantes") from exc

    return checkout_session.url


async def process_checkout_completed(session: AsyncSession, stripe_session: dict) -> None:
    """Credita a compra (cadastro novo ou recompra de tenant existente) a
    partir da metadata da sessão paga.

    Idempotente: uma sessão já processada (mesmo id) não cria duplicata.
    """
    session_id = stripe_session["id"]
    already_processed = await session.scalar(
        select(CreditTransaction.id).where(CreditTransaction.stripe_payment_id == session_id)
    )
    if already_processed is not None:
        logger.info("Webhook duplicado, ignorando | session=%s", session_id)
        return

    # stripe_session é um StripeObject real (não um dict): não implementa
    # .get(), só acesso via []/in — to_dict() normaliza pra dict puro.
    raw_metadata = stripe_session["metadata"] if "metadata" in stripe_session else {}
    metadata = raw_metadata.to_dict() if hasattr(raw_metadata, "to_dict") else dict(raw_metadata)

    if metadata.get("flow") == "recompra":
        await _process_recompra(session, session_id, metadata)
        return

    await _process_signup(session, session_id, metadata)


async def _process_signup(session: AsyncSession, session_id: str, metadata: dict) -> None:
    tenant_name = metadata.get("tenant_name")
    email = metadata.get("email")
    password_hash = metadata.get("password_hash")
    credit_package_id = metadata.get("credit_package_id")
    if not all([tenant_name, email, password_hash, credit_package_id]):
        logger.error("Metadata incompleta no checkout.session.completed | session=%s", session_id)
        return

    try:
        package_id = uuid.UUID(credit_package_id)
    except ValueError:
        logger.error(
            "credit_package_id malformado no checkout.session.completed | session=%s", session_id
        )
        return

    package = await session.get(CreditPackage, package_id)
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

    # Mesma transação do tenant/user/transação — sem isso, o tenant novo
    # nasce sem nenhum agente (C2 do review final da Etapa 1).
    for agent in build_default_agents(tenant.id):
        session.add(agent)

    # Mesma transação do tenant/user/transação — sem isso, o tenant novo
    # nasce sem assinatura, e POST /api/v1/agents e /knowledge-base/files
    # quebram (RuntimeError de get_active_subscription) até a Etapa 2
    # substituir isso por escolha real de plano no cadastro.
    session.add(await build_default_subscription(session, tenant.id))

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
        return

    # Auto-login pós-pagamento: token one-time, best-effort — se o Redis
    # falhar, a conta já existe e o usuário entra pelo /login normal.
    try:
        redis = await get_redis()
        await store_login_token(redis, session_id, user.id)
    except Exception as exc:
        logger.warning("Falha ao gravar token de auto-login | session=%s erro=%s", session_id, exc)


async def _process_recompra(session: AsyncSession, session_id: str, metadata: dict) -> None:
    tenant_id_raw = metadata.get("tenant_id")
    credit_package_id = metadata.get("credit_package_id")
    if not all([tenant_id_raw, credit_package_id]):
        logger.error("Metadata incompleta na recompra | session=%s", session_id)
        return

    try:
        tenant_id = uuid.UUID(tenant_id_raw)
        package_id = uuid.UUID(credit_package_id)
    except ValueError:
        logger.error("tenant_id/credit_package_id malformado na recompra | session=%s", session_id)
        return

    package = await session.get(CreditPackage, package_id)
    if package is None:
        logger.error("Pacote não encontrado ao processar recompra | session=%s", session_id)
        return

    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        logger.error("Tenant não encontrado ao processar recompra | session=%s", session_id)
        return

    tenant.credit_balance += package.credits_granted
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
            "Pagamento de recompra aprovado mas não foi possível gravar a "
            "transação | session=%s tenant_id=%s",
            session_id,
            tenant_id,
        )
