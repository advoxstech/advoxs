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
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.whatsapp import WhatsAppSendError, send_text_message
from app.core.config import settings
from app.core.crypto import decrypt_access_token, decrypt_tenant_secret
from app.models import (
    Conversation,
    EndCustomerBalance,
    EndCustomerCreditPackage,
    EndCustomerCreditTransaction,
    EndCustomerSubscription,
    Message,
    TenantBillingSettings,
    WhatsAppNumber,
)
from app.schemas.end_customer_billing import EndCustomerSummaryOut

logger = logging.getLogger(__name__)


class BillingNotConfiguredError(Exception):
    """Tenant sem cobrança habilitada ou sem secret key configurada."""


class InvalidPackageError(Exception):
    """Pacote inexistente, de outro tenant, ou inativo."""


class StripeApiError(Exception):
    """Falha ao criar a sessão de checkout na Stripe (rede ou resposta de erro)."""


class EndCustomerBalanceNotFoundError(Exception):
    """Esse contato nunca teve saldo registrado com o tenant."""


async def create_end_customer_checkout_session(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    contact_phone_number: str,
    package_id: uuid.UUID,
) -> str:
    billing_settings = await session.scalar(
        select(TenantBillingSettings).where(TenantBillingSettings.tenant_id == tenant_id)
    )
    if billing_settings is None or not billing_settings.enabled:
        raise BillingNotConfiguredError("Cobrança do cliente final não configurada pelo tenant")
    if (
        billing_settings.billing_provider == "standalone"
        and billing_settings.stripe_secret_key_encrypted is None
    ):
        raise BillingNotConfiguredError("Cobrança do cliente final não configurada pelo tenant")
    if billing_settings.billing_provider == "connect" and (
        billing_settings.stripe_account_id is None
        or billing_settings.stripe_account_status != "active"
    ):
        # stripe_account_id sozinho não basta: a capability precisa estar
        # "active" — cobre tanto a chamada direta à API (bypassando o
        # checkbox desabilitado do front) quanto uma conta que ficou active
        # uma vez e depois regrediu (downgrade de capability do lado da
        # Stripe). Esse é o momento de consequência real (geração do link de
        # checkout), então é aqui que a defesa importa de fato — ver design
        # doc 2026-07-24-stripe-connect-cobranca-cliente-final-design.md.
        raise BillingNotConfiguredError("Cobrança do cliente final não configurada pelo tenant")

    package = await session.scalar(
        select(EndCustomerCreditPackage).where(
            EndCustomerCreditPackage.id == package_id,
            EndCustomerCreditPackage.tenant_id == tenant_id,
        )
    )
    if package is None or not package.active:
        raise InvalidPackageError("Pacote de créditos inválido")

    if package.kind == "subscription":
        price_data = {
            "currency": "brl",
            "unit_amount": int(package.price_brl * 100),
            "product_data": {"name": package.name},
            "recurring": {"interval": "month"},
        }
        mode = "subscription"
        checkout_kind = "end_customer_subscription"
    else:
        price_data = {
            "currency": "brl",
            "unit_amount": int(package.price_brl * 100),
            "product_data": {"name": package.name},
        }
        mode = "payment"
        checkout_kind = "end_customer_purchase"

    line_items = [{"price_data": price_data, "quantity": 1}]
    metadata = {
        "tenant_id": str(tenant_id),
        "contact_phone_number": contact_phone_number,
        "package_id": str(package_id),
        "kind": checkout_kind,
    }

    try:
        if billing_settings.billing_provider == "connect":
            # Direct charge na conta conectada do tenant (v2, ver
            # app/services/stripe_connect.py): stripe_account= é uma
            # RequestOptions key extraída pelo próprio SDK do dict de
            # kwargs de checkout.Session.create (não vai no corpo JSON da
            # sessão) — equivalente ao header Stripe-Account. Confirmado
            # por introspecção de stripe._request_options.
            # extract_options_from_dict (stripe-python 15.3.0) e por uma
            # chamada real com api_key inválida, que devolveu
            # AuthenticationError (não TypeError) — ver relatório da
            # task. Zero comissão da plataforma: nenhum
            # application_fee_amount é passado.
            checkout_session = await asyncio.to_thread(
                stripe.checkout.Session.create,
                api_key=settings.stripe_connect_secret_key,
                stripe_account=billing_settings.stripe_account_id,
                mode=mode,
                line_items=line_items,
                metadata=metadata,
                success_url=f"{settings.web_app_url}/pagamento-confirmado",
                cancel_url=f"{settings.web_app_url}/pagamento-confirmado",
            )
        else:
            secret_key = decrypt_tenant_secret(billing_settings.stripe_secret_key_encrypted)
            checkout_session = await asyncio.to_thread(
                stripe.checkout.Session.create,
                api_key=secret_key,
                mode=mode,
                line_items=line_items,
                metadata=metadata,
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

    await _notify_end_customer(
        session,
        tenant_id,
        contact_phone_number,
        "Pagamento confirmado! Você já pode continuar a conversa.",
        exit_billing_gate=True,
    )


async def _notify_end_customer(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    contact_phone_number: str,
    text: str,
    *,
    exit_billing_gate: bool,
) -> None:
    """Notificação fixa via WhatsApp, best-effort — uma falha no envio nunca
    desfaz o efeito que já foi commitado antes desta chamada (crédito
    concedido, assinatura ativada/cancelada). Reaproveitada pela confirmação
    de compra avulsa, ativação e cancelamento de assinatura — só o texto e a
    decisão de sair do billing gate mudam entre os 3 casos.

    Além do aviso instantâneo (fixo, via WhatsApp direto), a conversa (se
    estiver em billing_gate e `exit_billing_gate=True`) volta direto pra
    "agent" — sem acionar o agents, já que o checkpoint do LangGraph nunca
    foi tocado por essa mudança de estado e a conversa retoma de onde estava
    (ou começa do zero pelo ponto de entrada, se nunca tinha sido atendida).
    """
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
                "Sem número/conversa pra notificar o cliente final | tenant=%s contato=%s",
                tenant_id,
                contact_phone_number,
            )
            return

        await send_text_message(
            phone_number_id=number.phone_number_id,
            access_token=decrypt_access_token(number.access_token_encrypted),
            to=contact_phone_number,
            text=text,
        )

        session.add(
            Message(
                conversation_id=conversation.id,
                tenant_id=tenant_id,
                sender_type="system",
                content=text,
                delivery_status="sent",
            )
        )
        conversation.last_message_at = datetime.now(UTC)

        if exit_billing_gate and conversation.state == "billing_gate":
            conversation.state = "agent"
            conversation.billing_gate_step = None
            conversation.billing_gate_retries = 0
        await session.commit()
    except WhatsAppSendError:
        logger.exception(
            "Falha ao notificar o cliente final via WhatsApp | tenant=%s contato=%s",
            tenant_id,
            contact_phone_number,
        )
    except Exception:
        logger.exception(
            "Erro inesperado ao notificar o cliente final | tenant=%s contato=%s",
            tenant_id,
            contact_phone_number,
        )


def _as_plain_dict(value: dict) -> dict:
    """Normaliza um payload de webhook Stripe pra um dict puro, recursivamente.

    `value` pode ser um `StripeObject` real (Session/Invoice/Subscription,
    entre outros) — não implementa `.get()`, só `[]`/`in` (o mesmo cuidado já
    documentado em `app/api/v1/webhooks/stripe_connect.py`). `.to_dict()`
    converte o objeto (e qualquer StripeObject aninhado dentro dele, ex:
    `metadata`, `lines.data[i].period`) pra dict/list puros, tornando seguro
    todo `.get()` chamado depois deste ponto. Quando `value` já é um dict
    puro (sempre o caso nos testes unitários, que mockam o payload assim),
    `dict(value)` só copia — os dicts/lists aninhados já são puros também."""
    return value.to_dict() if hasattr(value, "to_dict") else dict(value)


async def process_end_customer_subscription_created(
    session: AsyncSession, tenant_id: uuid.UUID, stripe_session: dict
) -> None:
    """Ativa a assinatura recorrente do cliente final e confirma via WhatsApp.

    Idempotente por stripe_subscription_id — mesmo padrão de idempotência já
    usado pra compra avulsa (lá por stripe_payment_id). `checkout.session.completed`
    é o mesmo evento Stripe usado pra compra avulsa — a diferenciação é só
    pela metadata (`kind`), nunca pelo `type` do evento, então esta função
    não faz nada quando a sessão não é de assinatura (`subscription` ausente
    ou metadata de outro kind)."""
    stripe_session = _as_plain_dict(stripe_session)
    subscription_id = stripe_session.get("subscription")
    if not subscription_id:
        return
    already_processed = await session.scalar(
        select(EndCustomerSubscription.id).where(
            EndCustomerSubscription.stripe_subscription_id == subscription_id
        )
    )
    if already_processed is not None:
        logger.info("Webhook de assinatura duplicado, ignorando | subscription=%s", subscription_id)
        return

    metadata = stripe_session.get("metadata") or {}
    if metadata.get("kind") != "end_customer_subscription":
        return

    contact_phone_number = metadata.get("contact_phone_number")
    package_id_raw = metadata.get("package_id")
    if not contact_phone_number or not package_id_raw:
        logger.error(
            "Metadata incompleta no webhook de assinatura | subscription=%s", subscription_id
        )
        return

    # Upsert por (tenant_id, contact_phone_number) — a unique constraint da
    # tabela (`uq_end_customer_subscriptions_tenant_contact`) é por esse par,
    # não por stripe_subscription_id sozinho. Um cliente que cancelou (a
    # linha sobrevive, só o status muda) e depois re-assina ganha um
    # stripe_subscription_id NOVO da Stripe — um INSERT cego colidiria com a
    # linha antiga e derrubaria o webhook com IntegrityError (Stripe reagenda
    # retry pra sempre, o cliente nunca é ativado). Por isso reaproveita a
    # linha existente em vez de inserir uma nova.
    existing = await session.scalar(
        select(EndCustomerSubscription).where(
            EndCustomerSubscription.tenant_id == tenant_id,
            EndCustomerSubscription.contact_phone_number == contact_phone_number,
        )
    )
    if existing is not None:
        existing.end_customer_credit_package_id = uuid.UUID(package_id_raw)
        existing.stripe_subscription_id = subscription_id
        existing.status = "active"
        # Reseta o período antigo: chegar neste branch sempre significa uma
        # assinatura NOVA (o check de idempotência por stripe_subscription_id
        # logo acima já retornou cedo pra evento duplicado da MESMA
        # assinatura) — current_period_end da linha reaproveitada é do ciclo
        # anterior (potencialmente já no passado, de antes do cancelamento) e
        # fica definicionalmente obsoleto. None reaproveita a mesma tolerância
        # que o path de INSERT já usa (ver comentário no bloco else abaixo) —
        # sem isso, um current_period_end passado bloqueia a query de
        # entitlement do worker até a próxima invoice.payment_succeeded
        # chegar, mesmo com status=active e pagamento confirmado.
        existing.current_period_end = None
        existing.updated_at = datetime.now(UTC)
    else:
        # `current_period_end` fica None aqui de propósito: a Stripe não
        # garante que `checkout.session.completed` chega antes de
        # `invoice.payment_succeeded` do primeiro período — o período de
        # cobrança só é conhecido quando a invoice chega (ver
        # `_extract_period_end`/`process_end_customer_subscription_renewed`).
        # A query de entitlement consumida pelo worker (Task 5 do plano
        # 2026-07-25-assinatura-recorrente-cliente-final.md,
        # `_load_context`) é deliberadamente escrita como
        # `status == "active" AND (current_period_end IS NULL OR
        # current_period_end >= now())` — trata NULL como "confia no status
        # active, ainda sem info de período" em vez de "inativo", cobrindo
        # exatamente essa corrida.
        session.add(
            EndCustomerSubscription(
                tenant_id=tenant_id,
                contact_phone_number=contact_phone_number,
                end_customer_credit_package_id=uuid.UUID(package_id_raw),
                stripe_subscription_id=subscription_id,
                status="active",
            )
        )
    await session.commit()

    await _notify_end_customer(
        session,
        tenant_id,
        contact_phone_number,
        "Assinatura ativada! Você já tem acesso ilimitado.",
        exit_billing_gate=True,
    )


def _extract_subscription_id(invoice: dict) -> str | None:
    """Extrai o id da subscription associada ao invoice, tentando as duas
    formas possíveis do payload.

    A partir de uma migração de versão da API da Stripe, `Invoice` deixou de
    expor `subscription` como campo de nível raiz — na versão pinada
    (`stripe.api_version == "2026-06-24.dahlia"`, confirmado por grep em
    `_invoice.py` do SDK instalado) a única referência à subscription é
    `parent.subscription_details.subscription` (o campo `parent` generaliza
    de onde a invoice se origina — assinatura, invoice item avulso, etc). Sem
    esse fallback, `invoice.get("subscription")` é sempre `None` num payload
    real e `process_end_customer_subscription_renewed` retorna sem fazer
    nada — renovações nunca atualizariam nada, silenciosamente. Tenta a forma
    legada primeiro (retrocompatibilidade, caso um invoice em formato antigo
    chegue aqui por algum motivo) e cai pra forma atual em seguida. `invoice`
    já foi normalizado por `_as_plain_dict` antes desta chamada, então
    `.get()` é seguro em todos os níveis."""
    legacy = invoice.get("subscription")
    if legacy:
        return legacy
    parent = invoice.get("parent") or {}
    subscription_details = parent.get("subscription_details") or {}
    return subscription_details.get("subscription")


def _extract_period_end(invoice: dict) -> datetime | None:
    """Fim do ciclo de cobrança pago — fica em `lines.data[0].period.end`
    (unix timestamp), não no `period_end` de nível raiz do `Invoice`.

    `Invoice.period_end` EXISTE de fato como campo raiz no SDK instalado
    (`stripe-python` 15.3.0) — mas ele descreve a janela usada pra associar
    invoice items soltos a essa invoice, não o período de serviço
    efetivamente pago por uma assinatura. A própria orientação da Stripe
    (docs.stripe.com/api/invoices/object, seção `lines`) é usar o período do
    line item pra obter o período de serviço de cada price — cada `invoice
    line item` carrega seu próprio `period.start`/`period.end`, que é o dado
    correto pra `current_period_end` de uma assinatura. Assume que `invoice`
    já foi normalizado (recursivamente) pra dict puro pelo chamador — ver
    `_as_plain_dict`."""
    lines = invoice.get("lines") or {}
    lines_data = lines.get("data") or []
    if not lines_data:
        return None
    period = lines_data[0].get("period") or {}
    end_timestamp = period.get("end")
    if end_timestamp is None:
        return None
    return datetime.fromtimestamp(end_timestamp, UTC)


async def process_end_customer_subscription_renewed(
    session: AsyncSession, tenant_id: uuid.UUID, invoice: dict
) -> None:
    """Renovação mensal (`invoice.payment_succeeded`) — atualiza
    status/current_period_end, sem notificar o cliente (decisão deliberada:
    renovação silenciosa evita spam mensal)."""
    invoice = _as_plain_dict(invoice)
    subscription_id = _extract_subscription_id(invoice)
    if not subscription_id:
        return
    subscription = await session.scalar(
        select(EndCustomerSubscription).where(
            EndCustomerSubscription.tenant_id == tenant_id,
            EndCustomerSubscription.stripe_subscription_id == subscription_id,
        )
    )
    if subscription is None:
        logger.warning(
            "Renovação de assinatura desconhecida | tenant=%s subscription=%s",
            tenant_id,
            subscription_id,
        )
        return

    subscription.status = "active"
    period_end = _extract_period_end(invoice)
    if period_end is not None:
        subscription.current_period_end = period_end
    subscription.updated_at = datetime.now(UTC)
    await session.commit()


async def process_end_customer_subscription_status_changed(
    session: AsyncSession, tenant_id: uuid.UUID, subscription_payload: dict, *, notify_cancel: bool
) -> None:
    """`customer.subscription.deleted` (cancelamento, notifica) ou
    `customer.subscription.updated` (ex: past_due, não notifica)."""
    subscription_payload = _as_plain_dict(subscription_payload)
    subscription_id = subscription_payload.get("id")
    if not subscription_id:
        return
    subscription = await session.scalar(
        select(EndCustomerSubscription).where(
            EndCustomerSubscription.tenant_id == tenant_id,
            EndCustomerSubscription.stripe_subscription_id == subscription_id,
        )
    )
    if subscription is None:
        logger.warning(
            "Mudança de status de assinatura desconhecida | tenant=%s subscription=%s",
            tenant_id,
            subscription_id,
        )
        return

    subscription.status = subscription_payload.get("status", subscription.status)
    subscription.updated_at = datetime.now(UTC)
    contact_phone_number = subscription.contact_phone_number
    await session.commit()

    if notify_cancel:
        await _notify_end_customer(
            session,
            tenant_id,
            contact_phone_number,
            "Sua assinatura mensal foi cancelada — o atendimento volta a consumir "
            "créditos normalmente.",
            exit_billing_gate=False,
        )


async def zero_end_customer_balance(
    session: AsyncSession, tenant_id: uuid.UUID, contact_phone_number: str
) -> None:
    """Remoção manual do saldo de um cliente final, pelo escritório — se ele
    entrar em contato de novo, precisa comprar créditos novamente.

    FOR UPDATE trava a linha antes de zerar, mesmo padrão de lock usado pelo
    `worker` nos débitos — evita colidir com um consumo em andamento no
    mesmo contato. Lança uma transação tipo `adjustment` (já aceita no
    schema, nunca usada até aqui) pra manter o ledger auditável.
    """
    balance = await session.scalar(
        select(EndCustomerBalance)
        .where(
            EndCustomerBalance.tenant_id == tenant_id,
            EndCustomerBalance.contact_phone_number == contact_phone_number,
        )
        .with_for_update()
    )
    if balance is None:
        raise EndCustomerBalanceNotFoundError("Esse contato não tem saldo registrado")

    if balance.credit_balance == 0:
        return

    session.add(
        EndCustomerCreditTransaction(
            tenant_id=tenant_id,
            contact_phone_number=contact_phone_number,
            type="adjustment",
            amount_credits=-balance.credit_balance,
            description="Remoção manual de créditos pelo escritório",
        )
    )
    balance.credit_balance = 0
    balance.updated_at = datetime.now(UTC)
    await session.commit()


async def list_customers(
    session: AsyncSession, tenant_id: uuid.UUID, limit: int, offset: int
) -> list[EndCustomerSummaryOut]:
    """Saldo atual + total comprado/consumido por cliente final do tenant."""
    purchased = func.coalesce(
        func.sum(
            case(
                (
                    EndCustomerCreditTransaction.type == "purchase",
                    EndCustomerCreditTransaction.amount_credits,
                ),
                else_=0,
            )
        ),
        0,
    )
    consumed = func.coalesce(
        func.sum(
            case(
                (
                    EndCustomerCreditTransaction.type == "consumption",
                    EndCustomerCreditTransaction.amount_credits,
                ),
                else_=0,
            )
        ),
        0,
    )
    rows = (
        await session.execute(
            select(
                EndCustomerBalance.contact_phone_number,
                EndCustomerBalance.credit_balance,
                purchased,
                consumed,
            )
            .outerjoin(
                EndCustomerCreditTransaction,
                (EndCustomerCreditTransaction.tenant_id == EndCustomerBalance.tenant_id)
                & (
                    EndCustomerCreditTransaction.contact_phone_number
                    == EndCustomerBalance.contact_phone_number
                ),
            )
            .where(EndCustomerBalance.tenant_id == tenant_id)
            .group_by(EndCustomerBalance.contact_phone_number, EndCustomerBalance.credit_balance)
            .order_by(func.abs(consumed).desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    return [
        EndCustomerSummaryOut(
            contact_phone_number=contact_phone_number,
            credit_balance=credit_balance,
            total_purchased=total_purchased,
            total_consumed=abs(total_consumed),
        )
        for contact_phone_number, credit_balance, total_purchased, total_consumed in rows
    ]
