"""Entrega do follow-up manual, um destinatário por vez.

Cada destinatário é reservado no banco (`pending` → `processing`), enviado pela
Meta (modelo aprovado) ou pela Z-API (texto) e só então registrado na conversa,
como mensagem humana de quem criou o envio. Falhas de instabilidade voltam para
a fila com espera crescente; recusas definitivas ficam como `failed` com o motivo.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app import tables
from app.clients.agents import sync_context_to_agents
from app.clients.omni import OmniTemplateError
from app.clients.omni import send_template as send_omni_template
from app.clients.whatsapp import WhatsAppTemplateError, send_template_message
from app.clients.zapi import ZApiApiError, ZApiNetworkError, send_zapi_text_message
from app.crypto import decrypt_access_token
from app.db import open_system_session, open_tenant_session
from app.follow_ups import render_template, render_text, resolve_params, template_components
from app.safe_logging import safe_error

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_PER_RUN = 100
SEND_INTERVAL_SECONDS = 1.0
RETRY_DELAY = timedelta(seconds=30)
PROCESSING_LEASE = timedelta(minutes=10)
REENQUEUE_AFTER = timedelta(minutes=1)
RECOVERY_BATCH_SIZE = 50

r = tables.follow_up_recipients


class FollowUpSendError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


def job_id(batch_id: str) -> str:
    return f"follow-up:{batch_id}"


async def _claim_next(ctx: dict, tenant_id: str, batch_id: str):
    now = datetime.now(UTC)
    next_id = (
        select(r.c.id)
        .where(
            r.c.tenant_id == uuid.UUID(tenant_id),
            r.c.batch_id == uuid.UUID(batch_id),
            r.c.status == "pending",
            r.c.available_at <= now,
        )
        .order_by(r.c.created_at, r.c.phone)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    async with open_tenant_session(ctx["session_factory"], tenant_id) as session:
        row = (
            await session.execute(
                update(r)
                .where(r.c.id == next_id)
                .values(status="processing", attempts=r.c.attempts + 1, locked_at=now)
                .returning(r.c.id, r.c.phone, r.c.contact_name, r.c.attempts)
            )
        ).one_or_none()
        await session.commit()
    return row


async def _finish(
    ctx: dict,
    tenant_id: str,
    recipient_id: uuid.UUID,
    *,
    status: str,
    error: str | None = None,
    available_at: datetime | None = None,
) -> None:
    values: dict[str, object] = {"status": status, "locked_at": None, "error": error}
    if available_at is not None:
        values["available_at"] = available_at
    async with open_tenant_session(ctx["session_factory"], tenant_id) as session:
        await session.execute(update(r).where(r.c.id == recipient_id).values(**values))
        await session.commit()


async def _send(batch, number, phone: str, contact_name: str) -> str:
    """Envia e devolve o texto que o contato recebeu."""
    if number is None:
        raise FollowUpSendError("Número de WhatsApp desconectado.", retryable=False)
    if batch.provider == "zapi":
        if number.provider != "zapi":
            raise FollowUpSendError("O número conectado mudou de provedor.", retryable=False)
        content = render_text(batch.text, contact_name)
        client_token = (
            decrypt_access_token(number.zapi_client_token_encrypted)
            if number.zapi_client_token_encrypted
            else None
        )
        try:
            await send_zapi_text_message(
                instance_id=number.zapi_instance_id,
                token=decrypt_access_token(number.zapi_instance_token_encrypted),
                client_token=client_token,
                to=phone,
                text=content,
            )
        except ZApiNetworkError as exc:
            raise FollowUpSendError("Falha de rede ao chamar a Z-API.", retryable=True) from exc
        except ZApiApiError as exc:
            raise FollowUpSendError(str(exc)[:500], retryable=False) from exc
        return content

    if number.provider != batch.provider:
        raise FollowUpSendError("O número conectado mudou de provedor.", retryable=False)
    values = resolve_params(batch.params or [], contact_name)
    if batch.provider == "omni":
        try:
            await send_omni_template(
                channel_id=number.omni_channel_id,
                to=phone,
                name=batch.template["name"],
                language=batch.template["language"],
                components=template_components(batch.template, values),
            )
        except OmniTemplateError as exc:
            raise FollowUpSendError(str(exc), retryable=exc.retryable) from exc
        return render_template(batch.template, values)
    try:
        await send_template_message(
            phone_number_id=number.phone_number_id,
            access_token=decrypt_access_token(number.access_token_encrypted),
            to=phone,
            name=batch.template["name"],
            language=batch.template["language"],
            components=template_components(batch.template, values),
        )
    except WhatsAppTemplateError as exc:
        raise FollowUpSendError(str(exc), retryable=exc.retryable) from exc
    return render_template(batch.template, values)


async def _find_conversation(session, tenant_id: str, phone: str):
    return (
        await session.execute(
            select(tables.conversations.c.id, tables.conversations.c.deletion_requested_at)
            .where(
                tables.conversations.c.tenant_id == uuid.UUID(tenant_id),
                tables.conversations.c.is_test.is_(False),
                func.contact_phone_key(tables.conversations.c.contact_phone_number) == phone,
            )
            .limit(1)
        )
    ).one_or_none()


async def _record_message(ctx: dict, tenant_id: str, batch, recipient, content: str) -> None:
    """Registra o envio na conversa do contato, criando-a se ainda não existir."""
    now = datetime.now(UTC)
    async with open_tenant_session(ctx["session_factory"], tenant_id) as session:
        conversation = await _find_conversation(session, tenant_id, recipient.phone)
        conversation_id = conversation.id if conversation else None
        if conversation_id is None:
            conversation_id = (
                await session.execute(
                    insert(tables.conversations)
                    .values(tenant_id=uuid.UUID(tenant_id), contact_phone_number=recipient.phone)
                    .on_conflict_do_nothing(index_elements=["tenant_id", "contact_phone_number"])
                    .returning(tables.conversations.c.id)
                )
            ).scalar_one_or_none()
            if conversation_id is None:
                conversation_id = (await _find_conversation(session, tenant_id, recipient.phone)).id
        message_id = (
            await session.execute(
                insert(tables.messages)
                .values(
                    conversation_id=conversation_id,
                    tenant_id=uuid.UUID(tenant_id),
                    sender_type="human",
                    sender_user_id=batch.created_by_user_id,
                    sender_user_name=batch.created_by_name or "Equipe",
                    content=content,
                    delivery_status="sent",
                )
                .returning(tables.messages.c.id)
            )
        ).scalar_one()
        await session.execute(
            update(tables.conversations)
            .where(tables.conversations.c.id == conversation_id)
            .values(last_message_at=now)
        )
        await session.execute(
            update(r)
            .where(r.c.id == recipient.id)
            .values(
                status="sent",
                sent_at=now,
                locked_at=None,
                error=None,
                conversation_id=conversation_id,
                message_id=message_id,
            )
        )
        await session.commit()


async def _deliver(ctx: dict, tenant_id: str, batch, number, recipient) -> None:
    async with open_tenant_session(ctx["session_factory"], tenant_id) as session:
        conversation = await _find_conversation(session, tenant_id, recipient.phone)
    if conversation is not None and conversation.deletion_requested_at is not None:
        await _finish(ctx, tenant_id, recipient.id, status="skipped", error="Conversa em exclusão.")
        return

    try:
        try:
            content = await _send(batch, number, recipient.phone, recipient.contact_name)
        except FollowUpSendError:
            raise
        except Exception as exc:  # noqa: BLE001 — erro inesperado conta como tentativa
            logger.warning(
                "Erro inesperado no follow-up | tenant=%s recipient=%s error_type=%s",
                tenant_id,
                recipient.id,
                safe_error(exc),
            )
            raise FollowUpSendError(
                "Erro inesperado ao enviar. Tente novamente mais tarde.", retryable=True
            ) from exc
    except FollowUpSendError as exc:
        if exc.retryable and recipient.attempts < MAX_ATTEMPTS:
            await _finish(
                ctx,
                tenant_id,
                recipient.id,
                status="pending",
                error=str(exc),
                available_at=datetime.now(UTC) + RETRY_DELAY * recipient.attempts,
            )
        else:
            await _finish(ctx, tenant_id, recipient.id, status="failed", error=str(exc))
        return

    try:
        await _record_message(ctx, tenant_id, batch, recipient, content)
    except Exception as exc:  # noqa: BLE001 — a mensagem já saiu; nunca reenviar
        logger.error(
            "Follow-up enviado, mas não registrado | tenant=%s recipient=%s error_type=%s",
            tenant_id,
            recipient.id,
            safe_error(exc),
        )
        await _finish(
            ctx,
            tenant_id,
            recipient.id,
            status="sent",
            error="Enviado, mas não foi possível registrar na conversa.",
        )
        return

    try:
        await sync_context_to_agents(
            ctx["http"],
            tenant_id=tenant_id,
            contact_phone_number=recipient.phone,
            role="attendant",
            content=content,
        )
    except Exception as exc:  # noqa: BLE001 — contexto da IA é melhor esforço
        logger.warning(
            "Falha ao sincronizar follow-up com a IA | tenant=%s error_type=%s",
            tenant_id,
            safe_error(exc),
        )


async def deliver_follow_up_batch(ctx: dict, tenant_id: str, batch_id: str) -> None:
    """Entrega os destinatários disponíveis do lote, com intervalo entre envios."""
    async with open_tenant_session(ctx["session_factory"], tenant_id) as session:
        batch = (
            await session.execute(
                select(tables.follow_up_batches).where(
                    tables.follow_up_batches.c.id == uuid.UUID(batch_id),
                    tables.follow_up_batches.c.tenant_id == uuid.UUID(tenant_id),
                )
            )
        ).one_or_none()
        number = (
            await session.execute(
                select(tables.whatsapp_numbers).where(
                    tables.whatsapp_numbers.c.tenant_id == uuid.UUID(tenant_id),
                    tables.whatsapp_numbers.c.status == "connected",
                )
            )
        ).one_or_none()
    if batch is None:
        return

    for index in range(MAX_PER_RUN):
        recipient = await _claim_next(ctx, tenant_id, batch_id)
        if recipient is None:
            return
        if index:
            await asyncio.sleep(SEND_INTERVAL_SECONDS)
        await _deliver(ctx, tenant_id, batch, number, recipient)


async def recover_follow_up_batches(ctx: dict) -> None:
    """Libera destinatários travados e reenfileira lotes com envios pendentes."""
    now = datetime.now(UTC)
    b = tables.follow_up_batches
    async with open_system_session(ctx["system_session_factory"]) as session:
        stale = (r.c.status == "processing", r.c.locked_at < now - PROCESSING_LEASE)
        await session.execute(
            update(r)
            .where(*stale, r.c.attempts >= MAX_ATTEMPTS)
            .values(status="failed", locked_at=None, error="Envio interrompido várias vezes.")
        )
        await session.execute(
            update(r)
            .where(*stale)
            .values(status="pending", locked_at=None, available_at=now, error="worker interrompido")
        )
        rows = (
            await session.execute(
                select(b.c.id, b.c.tenant_id)
                .where(
                    b.c.id.in_(
                        select(r.c.batch_id).where(r.c.status == "pending", r.c.available_at <= now)
                    ),
                    or_(
                        b.c.last_enqueued_at.is_(None),
                        b.c.last_enqueued_at <= now - REENQUEUE_AFTER,
                    ),
                )
                .order_by(b.c.created_at)
                .limit(RECOVERY_BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
        ).all()
        if rows:
            await session.execute(
                update(b).where(b.c.id.in_([row.id for row in rows])).values(last_enqueued_at=now)
            )
        await session.commit()

    for row in rows:
        await ctx["redis"].enqueue_job(
            "deliver_follow_up_batch",
            tenant_id=str(row.tenant_id),
            batch_id=str(row.id),
            _job_id=job_id(str(row.id)),
        )
