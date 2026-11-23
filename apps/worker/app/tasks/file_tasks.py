"""Banco é a fonte das tarefas; Redis é apenas um disparador recuperável."""

import uuid
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path

from sqlalchemy import delete, or_, select, update

from app import tables
from app.config import settings
from app.db import open_system_session, open_tenant_session
from app.safe_logging import safe_error

LEASE = timedelta(minutes=35)  # maior que o timeout de ingestão (30 min)
MAX_ATTEMPTS = 5


def durable_task(operation):
    def decorate(function):
        @wraps(function)
        async def run(ctx, tenant_id, file_id):
            tasks = tables.knowledge_file_tasks
            now, token = datetime.now(UTC), uuid.uuid4()
            async with open_tenant_session(ctx["session_factory"], tenant_id) as session:
                # Mesmo arquivo: ingestão e limpeza não executam simultaneamente.
                await session.execute(
                    select(tables.knowledge_base_files.c.id)
                    .where(tables.knowledge_base_files.c.id == uuid.UUID(file_id))
                    .with_for_update()
                )
                busy = await session.scalar(
                    select(tasks.c.file_id).where(
                        tasks.c.file_id == uuid.UUID(file_id),
                        tasks.c.state == "running",
                        tasks.c.lease_until > now,
                    )
                )
                if busy:
                    return
                task = (
                    await session.execute(
                        update(tasks)
                        .where(
                            tasks.c.file_id == uuid.UUID(file_id),
                            tasks.c.operation == operation,
                            tasks.c.state.in_(("pending", "running")),
                            tasks.c.next_attempt_at <= now,
                        )
                        .values(
                            state="running",
                            lease_until=now + LEASE,
                            lease_token=token,
                            attempts=tasks.c.attempts + 1,
                            updated_at=now,
                        )
                        .returning(tasks.c.attempts)
                    )
                ).scalar_one_or_none()
                await session.commit()
            if task is None:
                return
            error = None
            try:
                if task > MAX_ATTEMPTS:
                    raise RuntimeError("Limite de tentativas atingido após interrupções")
                await function({**ctx, "job_try": task}, tenant_id, file_id)
            except Exception as exc:
                error = safe_error(exc)
            async with open_tenant_session(ctx["session_factory"], tenant_id) as session:
                result = (
                    await session.execute(
                        select(
                            tables.knowledge_base_files.c.status,
                            tables.knowledge_base_files.c.error_message,
                        ).where(tables.knowledge_base_files.c.id == uuid.UUID(file_id))
                    )
                ).one_or_none()
                terminal_error = result is not None and result.status == "error" and error is None
                failed = terminal_error or (error is not None and task >= MAX_ATTEMPTS)
                state = "failed" if failed else "pending" if error else "completed"
                finalized = await session.execute(
                    update(tasks)
                    .where(
                        tasks.c.file_id == uuid.UUID(file_id),
                        tasks.c.operation == operation,
                        tasks.c.lease_token == token,
                    )
                    .values(
                        state=state,
                        lease_until=None,
                        lease_token=None,
                        last_error=result.error_message if terminal_error else error,
                        next_attempt_at=now + timedelta(seconds=min(15 * 2**task, 900)),
                        updated_at=datetime.now(UTC),
                    )
                    .returning(tasks.c.file_id)
                )
                if failed and not terminal_error and finalized.scalar_one_or_none() is not None:
                    await session.execute(
                        update(tables.knowledge_base_files)
                        .where(
                            tables.knowledge_base_files.c.id == uuid.UUID(file_id),
                        )
                        .values(
                            status="error",
                            error_message=(
                                "Falha na limpeza. Tente excluir novamente."
                                if operation == "delete"
                                else "Falha no processamento após várias tentativas. "
                                "Tente reprocessar."
                            ),
                        )
                    )
                await session.commit()

        return run

    return decorate


async def recover_file_tasks(ctx):
    tasks, files = tables.knowledge_file_tasks, tables.knowledge_base_files
    now = datetime.now(UTC)
    async with open_system_session(ctx["system_session_factory"]) as session:
        rows = (
            await session.execute(
                select(
                    tasks.c.file_id,
                    tasks.c.tenant_id,
                    tasks.c.operation,
                    files.c.public_document_id,
                )
                .outerjoin(files, files.c.id == tasks.c.file_id)
                .where(
                    tasks.c.state.in_(("pending", "running")),
                    tasks.c.next_attempt_at <= now,
                    or_(tasks.c.lease_until.is_(None), tasks.c.lease_until < now),
                )
                .order_by(tasks.c.next_attempt_at)
                .limit(100)
            )
        ).all()
    for row in rows:
        name = (
            "delete_knowledge_file"
            if row.operation == "delete"
            else "copy_public_document"
            if row.public_document_id
            else "ingest_knowledge_base_file"
        )
        await ctx["redis"].enqueue_job(
            name,
            tenant_id=str(row.tenant_id),
            file_id=str(row.file_id),
            _job_id=f"file-task:{row.operation}:{row.file_id}",
            _queue_name="arq:ingestion",
        )


@durable_task("delete")
async def delete_knowledge_file(ctx, tenant_id, file_id):
    files = tables.knowledge_base_files
    async with open_tenant_session(ctx["session_factory"], tenant_id) as session:
        row = (
            await session.execute(
                select(files).where(
                    files.c.id == uuid.UUID(file_id),
                    files.c.deletion_requested_at.is_not(None),
                )
            )
        ).one_or_none()
        if row is None:
            return
    response = await ctx["rag_http"].delete(
        "/documents/users/delete",
        params={"tenant_id": tenant_id, "docs_ids": [file_id]},
        headers={"Authorization": settings.rag_api_key},
    )
    response.raise_for_status()
    (Path(settings.kb_upload_dir) / tenant_id / file_id).unlink(missing_ok=True)
    async with open_tenant_session(ctx["session_factory"], tenant_id) as session:
        # Histórico e tentativas de atualização podem referenciar a versão anterior.
        await session.execute(
            update(files)
            .where(files.c.replaces_file_id == uuid.UUID(file_id))
            .values(replaces_file_id=None)
        )
        await session.execute(
            delete(files).where(
                files.c.id == uuid.UUID(file_id),
                files.c.deletion_requested_at.is_not(None),
            )
        )
        await session.commit()
