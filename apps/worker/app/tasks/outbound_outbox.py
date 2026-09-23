"""Entrega e recuperação das respostas já persistidas pelo worker."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from arq.worker import Retry
from sqlalchemy import or_, select, update

from app import tables
from app.clients.whatsapp import send_document_message, send_text_message
from app.clients.zapi import send_zapi_document_message, send_zapi_text_message
from app.crypto import decrypt_access_token
from app.db import open_system_session, open_tenant_session
from app.safe_logging import safe_error

logger = logging.getLogger(__name__)

MAX_DELIVERY_TRIES = 5
RECOVERY_BATCH_SIZE = 100
REENQUEUE_AFTER = timedelta(minutes=1)
PROCESSING_LEASE = timedelta(minutes=15)


async def enqueue_outbound_message_jobs(ctx: dict, job_ids: list[uuid.UUID]) -> None:
    """Dispara entregas após o commit; o banco continua sendo a fonte de verdade."""
    if not job_ids or "redis" not in ctx:
        return

    now = datetime.now(UTC)
    async with open_system_session(ctx["system_session_factory"]) as session:
        rows = (
            await session.execute(
                select(tables.outbound_message_jobs.c.id, tables.outbound_message_jobs.c.tenant_id)
                .where(
                    tables.outbound_message_jobs.c.id.in_(job_ids),
                    tables.outbound_message_jobs.c.status == "pending",
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            await session.execute(
                update(tables.outbound_message_jobs)
                .where(tables.outbound_message_jobs.c.id == row.id)
                .values(last_enqueued_at=now)
            )
        await session.commit()

    for row in rows:
        await ctx["redis"].enqueue_job(
            "deliver_outbound_message", tenant_id=str(row.tenant_id), job_id=str(row.id)
        )


async def _claim_job(ctx: dict, tenant_id: str, job_id: str) -> bool:
    async with open_system_session(ctx["system_session_factory"]) as session:
        claimed = await session.execute(
            update(tables.outbound_message_jobs)
            .where(
                tables.outbound_message_jobs.c.id == uuid.UUID(job_id),
                tables.outbound_message_jobs.c.tenant_id == uuid.UUID(tenant_id),
                tables.outbound_message_jobs.c.status == "pending",
                tables.outbound_message_jobs.c.available_at <= datetime.now(UTC),
            )
            .values(
                status="processing",
                attempts=tables.outbound_message_jobs.c.attempts + 1,
                locked_at=datetime.now(UTC),
                last_error=None,
            )
            .returning(tables.outbound_message_jobs.c.id)
        )
        await session.commit()
    return claimed.scalar_one_or_none() is not None


async def _set_job_status(ctx: dict, job_id: str, *, status: str, error: str | None = None) -> None:
    values: dict[str, object] = {
        "status": status,
        "locked_at": None,
        "completed_at": (
            datetime.now(UTC) if status in {"delivered", "failed", "cancelled"} else None
        ),
        "last_error": (error or "")[:1000] if error else None,
    }
    if status == "pending":
        values["available_at"] = datetime.now(UTC)

    async with open_system_session(ctx["system_session_factory"]) as session:
        await session.execute(
            update(tables.outbound_message_jobs)
            .where(tables.outbound_message_jobs.c.id == uuid.UUID(job_id))
            .values(**values)
        )
        await session.commit()


async def _load_delivery(session, message_id: str):
    return (
        await session.execute(
            select(
                tables.messages.c.content,
                tables.messages.c.media_url,
                tables.conversations.c.contact_phone_number,
                tables.whatsapp_numbers.c.provider,
                tables.whatsapp_numbers.c.phone_number_id,
                tables.whatsapp_numbers.c.access_token_encrypted,
                tables.whatsapp_numbers.c.zapi_instance_id,
                tables.whatsapp_numbers.c.zapi_instance_token_encrypted,
                tables.whatsapp_numbers.c.zapi_client_token_encrypted,
            )
            .join(
                tables.conversations,
                tables.conversations.c.id == tables.messages.c.conversation_id,
            )
            .join(
                tables.whatsapp_numbers,
                tables.whatsapp_numbers.c.tenant_id == tables.messages.c.tenant_id,
            )
            .where(
                tables.messages.c.id == uuid.UUID(message_id),
                tables.whatsapp_numbers.c.status == "connected",
            )
        )
    ).one_or_none()


async def _cancel_if_automation_paused(
    session, conversation_id: uuid.UUID, message_id: uuid.UUID
) -> bool:
    """Cancela a entrega sob o mesmo lock usado pela troca de atendimento."""
    state = (
        await session.execute(
            select(tables.conversations.c.state)
            .where(tables.conversations.c.id == conversation_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if state == "agent":
        return False

    await session.execute(
        update(tables.messages)
        .where(
            tables.messages.c.id == message_id,
            tables.messages.c.delivery_status == "pending",
        )
        .values(delivery_status="cancelled")
    )
    await session.commit()
    return True


def _document_filename(content: str) -> str | None:
    prefix = "📄 "
    return content.removeprefix(prefix) if content.startswith(prefix) else None


async def _send_delivery(delivery) -> None:
    if delivery.provider == "zapi":
        token = decrypt_access_token(delivery.zapi_instance_token_encrypted)
        client_token = (
            decrypt_access_token(delivery.zapi_client_token_encrypted)
            if delivery.zapi_client_token_encrypted
            else None
        )
        if delivery.media_url:
            await send_zapi_document_message(
                delivery.zapi_instance_id,
                token,
                client_token,
                delivery.contact_phone_number,
                delivery.media_url,
                _document_filename(delivery.content),
            )
        else:
            await send_zapi_text_message(
                delivery.zapi_instance_id,
                token,
                client_token,
                delivery.contact_phone_number,
                delivery.content,
            )
        return

    access_token = decrypt_access_token(delivery.access_token_encrypted)
    if delivery.media_url:
        await send_document_message(
            delivery.phone_number_id,
            access_token,
            delivery.contact_phone_number,
            delivery.media_url,
            _document_filename(delivery.content),
        )
    else:
        await send_text_message(
            delivery.phone_number_id,
            access_token,
            delivery.contact_phone_number,
            delivery.content,
        )


async def deliver_outbound_message(ctx: dict, tenant_id: str, job_id: str) -> None:
    """Entrega uma resposta pronta. Nunca chama o serviço de agentes."""
    if not await _claim_job(ctx, tenant_id, job_id):
        return

    message_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    try:
        async with open_system_session(ctx["system_session_factory"]) as session:
            job = (
                await session.execute(
                    select(
                        tables.outbound_message_jobs.c.message_id,
                        tables.outbound_message_jobs.c.conversation_id,
                    ).where(tables.outbound_message_jobs.c.id == uuid.UUID(job_id))
                )
            ).one()
            message_id = job.message_id
            conversation_id = job.conversation_id

        async with open_tenant_session(ctx["session_factory"], tenant_id) as session:
            if await _cancel_if_automation_paused(session, conversation_id, message_id):
                await _set_job_status(
                    ctx,
                    job_id,
                    status="cancelled",
                    error="automação pausada antes da entrega",
                )
                logger.info(
                    "Entrega cancelada porque a automação foi pausada | tenant=%s job=%s",
                    tenant_id,
                    job_id,
                )
                return
            delivery = await _load_delivery(session, str(message_id))
            if delivery is None:
                raise RuntimeError("mensagem ou número WhatsApp conectado não encontrado")
            await _send_delivery(delivery)
            await session.execute(
                update(tables.messages)
                .where(tables.messages.c.id == message_id)
                .values(delivery_status="sent")
            )
            remaining_delivery = (
                await session.execute(
                    select(tables.outbound_message_jobs.c.id)
                    .where(
                        tables.outbound_message_jobs.c.conversation_id == conversation_id,
                        tables.outbound_message_jobs.c.id != uuid.UUID(job_id),
                        tables.outbound_message_jobs.c.status.in_(("pending", "processing")),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if remaining_delivery is None:
                await session.execute(
                    update(tables.conversations)
                    .where(tables.conversations.c.id == conversation_id)
                    .values(automation_status="idle")
                )
            await session.commit()
    except Exception as exc:
        if ctx.get("job_try", 1) < MAX_DELIVERY_TRIES:
            await _set_job_status(ctx, job_id, status="pending", error=safe_error(exc))
            logger.warning(
                "Falha na entrega, reagendando sem gerar nova resposta | "
                "tenant=%s job=%s error_type=%s",
                tenant_id,
                job_id,
                safe_error(exc),
            )
            raise Retry(defer=ctx.get("job_try", 1) * 10) from exc

        await _set_job_status(ctx, job_id, status="failed", error=safe_error(exc))
        if message_id is not None and conversation_id is not None:
            async with open_tenant_session(ctx["session_factory"], tenant_id) as session:
                await session.execute(
                    update(tables.messages)
                    .where(tables.messages.c.id == message_id)
                    .values(delivery_status="failed")
                )
                await session.execute(
                    update(tables.conversations)
                    .where(tables.conversations.c.id == conversation_id)
                    .values(automation_status="failed")
                )
                await session.commit()
        logger.error(
            "Falha definitiva ao entregar resposta | tenant=%s job=%s error_type=%s",
            tenant_id,
            job_id,
            safe_error(exc),
        )
        return

    await _set_job_status(ctx, job_id, status="delivered")


async def recover_outbound_message_jobs(ctx: dict) -> None:
    """Reenvia pendências e libera trabalhos de entrega abandonados."""
    now = datetime.now(UTC)
    async with open_system_session(ctx["system_session_factory"]) as session:
        await session.execute(
            update(tables.outbound_message_jobs)
            .where(
                tables.outbound_message_jobs.c.status == "processing",
                tables.outbound_message_jobs.c.locked_at < now - PROCESSING_LEASE,
            )
            .values(
                status="pending",
                locked_at=None,
                available_at=now,
                last_error="worker interrompido",
            )
        )
        rows = (
            await session.execute(
                select(
                    tables.outbound_message_jobs.c.id,
                    tables.outbound_message_jobs.c.tenant_id,
                )
                .where(
                    tables.outbound_message_jobs.c.status == "pending",
                    tables.outbound_message_jobs.c.available_at <= now,
                    or_(
                        tables.outbound_message_jobs.c.last_enqueued_at.is_(None),
                        tables.outbound_message_jobs.c.last_enqueued_at <= now - REENQUEUE_AFTER,
                    ),
                )
                .order_by(tables.outbound_message_jobs.c.created_at)
                .limit(RECOVERY_BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            await session.execute(
                update(tables.outbound_message_jobs)
                .where(tables.outbound_message_jobs.c.id == row.id)
                .values(last_enqueued_at=now)
            )
        await session.commit()

    for row in rows:
        await ctx["redis"].enqueue_job(
            "deliver_outbound_message", tenant_id=str(row.tenant_id), job_id=str(row.id)
        )
