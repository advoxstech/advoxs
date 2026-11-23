"""Falhas entre banco, fila e RAG, contra PostgreSQL descartável."""

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.tasks.file_tasks import delete_knowledge_file, durable_task, recover_file_tasks


@pytest.fixture
async def database():
    url = os.getenv("FILE_TASKS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable FILE_TASKS_TEST_DATABASE_URL")
    schema = "file_tasks_" + uuid.uuid4().hex
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    migration_path = (
        Path(__file__).parents[3] / "api/alembic/versions" / "0055_durable_file_tasks.py"
    )
    spec = importlib.util.spec_from_file_location("file_tasks_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def upgrade(connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        await conn.execute(text("CREATE TABLE tenants(id uuid PRIMARY KEY)"))
        await conn.execute(
            text("""CREATE TABLE knowledge_base_files (
            id uuid PRIMARY KEY, tenant_id uuid REFERENCES tenants(id), status text,
            public_document_id uuid, replaces_file_id uuid REFERENCES knowledge_base_files(id),
            error_message text, superseded_at timestamptz, filename text, category text,
            drive_file_id text, imported_at timestamptz, pages_total integer,
            partial_pages integer[])""")
        )
        await conn.run_sync(upgrade)
    tenant, file = uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO tenants VALUES (:t)"), {"t": tenant})
        await conn.execute(
            text(
                "INSERT INTO knowledge_base_files(id,tenant_id,status) VALUES (:f,:t,'processing')"
            ),
            {"f": file, "t": tenant},
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ctx = {
        "session_factory": factory,
        "system_session_factory": factory,
        "redis": AsyncMock(),
        "rag_http": AsyncMock(),
    }
    try:
        yield engine, ctx, str(tenant), str(file)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


async def test_acceptance_without_redis_is_recovered(database):
    engine, ctx, tenant, file = database
    await recover_file_tasks(ctx)
    assert ctx["redis"].enqueue_job.await_args.kwargs["file_id"] == file
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT state FROM knowledge_file_tasks")) == "pending"


async def test_package_copy_is_recovered(database):
    engine, ctx, tenant, file = database
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE knowledge_base_files SET public_document_id=:id"), {"id": uuid.uuid4()}
        )
    await recover_file_tasks(ctx)
    assert ctx["redis"].enqueue_job.await_args.args[0] == "copy_public_document"


async def test_one_execution_and_stale_lease_recovery(database):
    engine, ctx, tenant, file = database
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    @durable_task("ingest")
    async def operation(ctx, tenant, file):
        calls.append(file)
        entered.set()
        await release.wait()

    first = asyncio.create_task(operation(ctx, tenant, file))
    await entered.wait()
    await operation(ctx, tenant, file)
    assert len(calls) == 1
    release.set()
    await first
    # Simulate a process killed after claiming a task.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE knowledge_file_tasks SET state='running', "
                "lease_until=now()-interval '1 minute', next_attempt_at=now()"
            )
        )
    await operation(ctx, tenant, file)
    assert len(calls) == 2


async def test_deletion_intent_rolls_back_without_touching_rag(database):
    engine, ctx, tenant, file = database
    async with engine.connect() as conn:
        await conn.execute(text("UPDATE knowledge_base_files SET deletion_requested_at=now()"))
        await conn.rollback()
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM knowledge_file_tasks WHERE operation='delete'")
            )
            == 0
        )
    await delete_knowledge_file(ctx, tenant, file)
    ctx["rag_http"].delete.assert_not_awaited()


async def test_failed_cleanup_retries_and_is_idempotent(database, tmp_path, monkeypatch):
    engine, ctx, tenant, file = database
    from app.tasks.file_tasks import settings

    monkeypatch.setattr(settings, "kb_upload_dir", str(tmp_path))
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE knowledge_base_files SET status='ready', deletion_requested_at=now()")
        )
    request = httpx.Request("DELETE", "http://rag/documents/users/delete")
    ctx["rag_http"].delete.side_effect = httpx.ConnectError("offline", request=request)
    await delete_knowledge_file(ctx, tenant, file)
    async with engine.begin() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_base_files")) == 1
        assert (
            await conn.scalar(
                text("SELECT state FROM knowledge_file_tasks WHERE operation='delete'")
            )
            == "pending"
        )
        await conn.execute(text("UPDATE knowledge_file_tasks SET next_attempt_at=now()"))
    ctx["rag_http"].delete.side_effect = None
    ctx["rag_http"].delete.return_value = httpx.Response(200, request=request)
    await delete_knowledge_file(ctx, tenant, file)
    await delete_knowledge_file(ctx, tenant, file)
    assert ctx["rag_http"].delete.await_count == 2
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_base_files")) == 0


async def test_database_failure_after_external_delete_is_recoverable(
    database, tmp_path, monkeypatch
):
    engine, ctx, tenant, file = database
    from app.tasks.file_tasks import settings

    monkeypatch.setattr(settings, "kb_upload_dir", str(tmp_path))
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE knowledge_base_files SET status='ready', deletion_requested_at=now()")
        )
    request = httpx.Request("DELETE", "http://rag/documents/users/delete")
    ctx["rag_http"].delete.return_value = httpx.Response(200, request=request)
    fail = True

    def fail_database_once(conn, cursor, statement, params, context, many):
        nonlocal fail
        if fail and statement.startswith("DELETE FROM knowledge_base_files"):
            fail = False
            raise RuntimeError("database interrupted after external deletion")

    event.listen(engine.sync_engine, "before_cursor_execute", fail_database_once)
    try:
        await delete_knowledge_file(ctx, tenant, file)
        async with engine.begin() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM knowledge_base_files")) == 1
            await conn.execute(text("UPDATE knowledge_file_tasks SET next_attempt_at=now()"))
        await delete_knowledge_file(ctx, tenant, file)
        assert ctx["rag_http"].delete.await_count == 2
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM knowledge_base_files")) == 0
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", fail_database_once)


async def test_attempts_end_in_error_and_manual_retry_resets_task(database):
    engine, ctx, tenant, file = database

    @durable_task("ingest")
    async def broken(ctx, tenant, file):
        raise RuntimeError("offline")

    for _ in range(5):
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE knowledge_file_tasks SET next_attempt_at=now()"))
        await broken(ctx, tenant, file)
    async with engine.begin() as conn:
        assert await conn.scalar(text("SELECT state FROM knowledge_file_tasks")) == "failed"
        assert await conn.scalar(text("SELECT status FROM knowledge_base_files")) == "error"
        await conn.execute(text("UPDATE knowledge_base_files SET status='processing'"))
        assert await conn.scalar(text("SELECT attempts FROM knowledge_file_tasks")) == 0
        assert await conn.scalar(text("SELECT state FROM knowledge_file_tasks")) == "pending"


async def test_task_policy_isolates_offices(database):
    engine, ctx, tenant, file = database
    role = "file_reader_" + uuid.uuid4().hex
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text(f'CREATE ROLE "{role}"'))
            schema = await conn.scalar(text("SELECT current_schema()"))
            await conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
            await conn.execute(text(f'GRANT SELECT ON knowledge_file_tasks TO "{role}"'))
            await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
            await conn.execute(text("SELECT set_config('app.tenant_id',:t,true)"), {"t": tenant})
            assert await conn.scalar(text("SELECT count(*) FROM knowledge_file_tasks")) == 1
            await conn.execute(
                text("SELECT set_config('app.tenant_id',:t,true)"), {"t": str(uuid.uuid4())}
            )
            assert await conn.scalar(text("SELECT count(*) FROM knowledge_file_tasks")) == 0
            await conn.rollback()  # Também remove o papel criado para este teste.
