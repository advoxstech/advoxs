"""Exclusão durável de conversas, checkpoints e anexos pessoais no RAG."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from arq.worker import Retry
from sqlalchemy import delete, func, or_, select, update

from app import tables
from app.config import settings
from app.db import open_system_session
from app.safe_logging import safe_error

logger = logging.getLogger(__name__)

RECOVERY_BATCH_SIZE = 100
REENQUEUE_AFTER = timedelta(minutes=1)
PROCESSING_LEASE = timedelta(minutes=15)
MAX_RETRY_DELAY_SECONDS = 15 * 60


async def _claim_job(ctx: dict, tenant_id: str, job_id: str) -> bool:
    now = datetime.now(UTC)
    async with open_system_session(ctx["system_session_factory"]) as session:
        claimed = await session.execute(
            update(tables.conversation_cleanup_jobs)
            .where(
                tables.conversation_cleanup_jobs.c.id == uuid.UUID(job_id),
                tables.conversation_cleanup_jobs.c.tenant_id == uuid.UUID(tenant_id),
                tables.conversation_cleanup_jobs.c.status == "pending",
                tables.conversation_cleanup_jobs.c.available_at <= now,
            )
            .values(
                status="processing",
                attempts=tables.conversation_cleanup_jobs.c.attempts + 1,
                locked_at=now,
                last_error=None,
            )
            .returning(tables.conversation_cleanup_jobs.c.id)
        )
        await session.commit()
    return claimed.scalar_one_or_none() is not None


async def _release_job(ctx: dict, job_id: str, exc: Exception) -> int:
    delay = min(2 ** min(ctx.get("job_try", 1), 9) * 5, MAX_RETRY_DELAY_SECONDS)
    async with open_system_session(ctx["system_session_factory"]) as session:
        await session.execute(
            update(tables.conversation_cleanup_jobs)
            .where(tables.conversation_cleanup_jobs.c.id == uuid.UUID(job_id))
            .values(
                status="pending",
                available_at=datetime.now(UTC) + timedelta(seconds=delay),
                locked_at=None,
                last_error=safe_error(exc)[:1000],
            )
        )
        await session.commit()
    return delay


async def _load_cleanup(ctx: dict, tenant_id: str, job_id: str):
    async with open_system_session(ctx["system_session_factory"]) as session:
        return (
            await session.execute(
                select(
                    tables.conversation_cleanup_jobs.c.conversation_id,
                    tables.conversation_cleanup_jobs.c.message_ids,
                    tables.conversation_cleanup_jobs.c.attachment_document_ids,
                    tables.conversations.c.contact_phone_number,
                )
                .join(
                    tables.conversations,
                    tables.conversations.c.id == tables.conversation_cleanup_jobs.c.conversation_id,
                )
                .where(
                    tables.conversation_cleanup_jobs.c.id == uuid.UUID(job_id),
                    tables.conversation_cleanup_jobs.c.tenant_id == uuid.UUID(tenant_id),
                    tables.conversation_cleanup_jobs.c.status == "processing",
                )
            )
        ).one_or_none()


async def _delete_external_state(ctx: dict, tenant_id: str, cleanup) -> None:
    thread_id = f"{tenant_id}:{cleanup.contact_phone_number}"
    checkpoint_response = await ctx["http"].delete(
        f"/conversations/{quote(thread_id, safe='')}",
        headers={"Authorization": settings.agents_api_key} if settings.agents_api_key else {},
    )
    checkpoint_response.raise_for_status()

    if cleanup.attachment_document_ids:
        rag_response = await ctx["rag_http"].delete(
            "/documents/users/delete",
            params={
                "tenant_id": tenant_id,
                "docs_ids": cleanup.attachment_document_ids,
            },
            headers={"Authorization": settings.rag_api_key},
        )
        rag_response.raise_for_status()


async def _finalize_cleanup(ctx: dict, tenant_id: str, job_id: str, cleanup) -> str | None:
    conversation_id = cleanup.conversation_id
    message_ids = [uuid.UUID(value) for value in cleanup.message_ids]

    async with open_system_session(ctx["system_session_factory"]) as session:
        job = (
            await session.execute(
                select(tables.conversation_cleanup_jobs.c.id)
                .where(
                    tables.conversation_cleanup_jobs.c.id == uuid.UUID(job_id),
                    tables.conversation_cleanup_jobs.c.tenant_id == uuid.UUID(tenant_id),
                    tables.conversation_cleanup_jobs.c.status == "processing",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            return None

        await session.execute(
            delete(tables.conversation_processing_locks).where(
                tables.conversation_processing_locks.c.conversation_id == conversation_id
            )
        )
        if message_ids:
            await session.execute(
                delete(tables.inbound_message_jobs).where(
                    tables.inbound_message_jobs.c.message_id.in_(message_ids)
                )
            )
            await session.execute(
                delete(tables.outbound_message_jobs).where(
                    tables.outbound_message_jobs.c.message_id.in_(message_ids)
                )
            )
            await session.execute(
                update(tables.credit_transactions)
                .where(tables.credit_transactions.c.related_message_id.in_(message_ids))
                .values(related_message_id=None)
            )
            await session.execute(
                update(tables.end_customer_credit_transactions)
                .where(
                    tables.end_customer_credit_transactions.c.related_message_id.in_(message_ids)
                )
                .values(related_message_id=None)
            )
            await session.execute(
                update(tables.usage_records)
                .where(tables.usage_records.c.related_message_id.in_(message_ids))
                .values(related_message_id=None)
            )
            await session.execute(
                delete(tables.messages).where(tables.messages.c.id.in_(message_ids))
            )

        remaining = await session.scalar(
            select(func.count())
            .select_from(tables.messages)
            .where(tables.messages.c.conversation_id == conversation_id)
        )
        await session.execute(
            delete(tables.conversation_cleanup_jobs).where(
                tables.conversation_cleanup_jobs.c.id == uuid.UUID(job_id)
            )
        )

        if remaining:
            pending = await session.scalar(
                select(func.count())
                .select_from(tables.inbound_message_jobs)
                .where(
                    tables.inbound_message_jobs.c.conversation_id == conversation_id,
                    tables.inbound_message_jobs.c.status == "pending",
                )
            )
            await session.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == conversation_id)
                .values(
                    deletion_requested_at=None,
                    state="agent",
                    automation_status="processing" if pending else "idle",
                    summary=None,
                    summary_generated_at=None,
                    human_last_seen_at=None,
                    billing_gate_step=None,
                    billing_gate_retries=0,
                    billing_gate_checkout_url=None,
                    current_agent_id=None,
                )
            )
            await session.commit()
            return str(conversation_id)

        await session.execute(
            update(tables.usage_records)
            .where(tables.usage_records.c.conversation_id == conversation_id)
            .values(conversation_id=None)
        )
        await session.execute(
            delete(tables.conversations).where(tables.conversations.c.id == conversation_id)
        )
        await session.commit()
    return None


async def process_conversation_cleanup(ctx: dict, tenant_id: str, job_id: str) -> None:
    if not await _claim_job(ctx, tenant_id, job_id):
        return
    try:
        cleanup = await _load_cleanup(ctx, tenant_id, job_id)
        if cleanup is None:
            return
        await _delete_external_state(ctx, tenant_id, cleanup)
        resumed_conversation_id = await _finalize_cleanup(ctx, tenant_id, job_id, cleanup)
        if resumed_conversation_id is not None:
            from app.tasks.messages import _enqueue_next_inbound_message_job

            await _enqueue_next_inbound_message_job(ctx, resumed_conversation_id)
    except Exception as exc:
        delay = await _release_job(ctx, job_id, exc)
        logger.warning(
            "Falha na exclusão de conversa; nova tentativa agendada | "
            "tenant=%s job=%s error_type=%s",
            tenant_id,
            job_id,
            safe_error(exc),
        )
        raise Retry(defer=delay) from exc


async def recover_conversation_cleanup_jobs(ctx: dict) -> None:
    now = datetime.now(UTC)
    async with open_system_session(ctx["system_session_factory"]) as session:
        await session.execute(
            update(tables.conversation_cleanup_jobs)
            .where(
                tables.conversation_cleanup_jobs.c.status == "processing",
                tables.conversation_cleanup_jobs.c.locked_at < now - PROCESSING_LEASE,
            )
            .values(status="pending", locked_at=None, available_at=now)
        )
        rows = (
            await session.execute(
                select(
                    tables.conversation_cleanup_jobs.c.id,
                    tables.conversation_cleanup_jobs.c.tenant_id,
                )
                .where(
                    tables.conversation_cleanup_jobs.c.status == "pending",
                    tables.conversation_cleanup_jobs.c.available_at <= now,
                    or_(
                        tables.conversation_cleanup_jobs.c.last_enqueued_at.is_(None),
                        tables.conversation_cleanup_jobs.c.last_enqueued_at < now - REENQUEUE_AFTER,
                    ),
                )
                .order_by(tables.conversation_cleanup_jobs.c.available_at)
                .limit(RECOVERY_BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
        ).all()
        if rows:
            await session.execute(
                update(tables.conversation_cleanup_jobs)
                .where(tables.conversation_cleanup_jobs.c.id.in_([row.id for row in rows]))
                .values(last_enqueued_at=now)
            )
        await session.commit()

    if "redis" not in ctx:
        return
    for row in rows:
        await ctx["redis"].enqueue_job(
            "process_conversation_cleanup",
            tenant_id=str(row.tenant_id),
            job_id=str(row.id),
        )
