"""Recuperação periódica da caixa de saída de mensagens recebidas."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update

from app import tables
from app.db import open_system_session

RECOVERY_BATCH_SIZE = 100
REENQUEUE_AFTER = timedelta(minutes=1)
PROCESSING_LEASE = timedelta(minutes=15)


async def recover_inbound_message_jobs(ctx: dict) -> None:
    """Reenvia pendências e libera jobs abandonados por worker interrompido."""
    now = datetime.now(UTC)
    async with open_system_session(ctx["system_session_factory"]) as session:
        # O lease protege contra worker encerrado no meio de uma geração. O
        # próximo job só poderá assumir a conversa depois que esta linha for
        # removida; apagar por idade evita atendimento travado para sempre.
        await session.execute(
            delete(tables.conversation_processing_locks).where(
                tables.conversation_processing_locks.c.locked_at < now - PROCESSING_LEASE
            )
        )
        await session.execute(
            update(tables.inbound_message_jobs)
            .where(
                tables.inbound_message_jobs.c.status == "processing",
                tables.inbound_message_jobs.c.locked_at < now - PROCESSING_LEASE,
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
                    tables.inbound_message_jobs.c.id,
                    tables.inbound_message_jobs.c.tenant_id,
                    tables.inbound_message_jobs.c.conversation_id,
                    tables.inbound_message_jobs.c.message_id,
                )
                .where(
                    tables.inbound_message_jobs.c.status == "pending",
                    tables.inbound_message_jobs.c.available_at <= now,
                    or_(
                        tables.inbound_message_jobs.c.last_enqueued_at.is_(None),
                        tables.inbound_message_jobs.c.last_enqueued_at <= now - REENQUEUE_AFTER,
                    ),
                )
                .order_by(tables.inbound_message_jobs.c.created_at)
                .limit(RECOVERY_BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            await session.execute(
                update(tables.inbound_message_jobs)
                .where(tables.inbound_message_jobs.c.id == row.id)
                .values(last_enqueued_at=now)
            )
        await session.commit()

    redis = ctx["redis"]
    for row in rows:
        await redis.enqueue_job(
            "process_inbound_message",
            tenant_id=str(row.tenant_id),
            conversation_id=str(row.conversation_id),
            message_id=str(row.message_id),
            job_id=str(row.id),
        )
