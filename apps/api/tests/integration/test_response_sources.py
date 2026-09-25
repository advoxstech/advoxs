"""Additive migration and message evidence under PostgreSQL tenant isolation.

Uses the CI's disposable database, never the configured application database.
"""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def migrate(connection, direction="upgrade"):
    path = Path(__file__).parents[2] / "alembic/versions/0039_message_response_sources.py"
    spec = importlib.util.spec_from_file_location("sources_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration, direction)()


async def test_migration_evidence_survives_delivery_updates_and_respects_rls():
    url = os.getenv("AGENT_VERSIONS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires an explicitly configured disposable PostgreSQL database")
    schema = f"sources_test_{uuid.uuid4().hex}"
    role = f"sources_reader_{uuid.uuid4().hex}"
    tenant, other = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(
                text("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY, tenant_id UUID NOT NULL,
                    content TEXT NOT NULL, delivery_status TEXT
                )
            """)
            )
            await conn.execute(text("ALTER TABLE messages ENABLE ROW LEVEL SECURITY"))
            await conn.execute(
                text("""
                CREATE POLICY tenant_isolation ON messages
                USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
            """)
            )
            await conn.execute(
                text("INSERT INTO messages VALUES (1, :tenant, 'old', 'sent')"), {"tenant": tenant}
            )
            await conn.run_sync(migrate)
            assert (
                await conn.scalar(text("SELECT response_sources FROM messages WHERE id=1")) is None
            )
            await conn.execute(
                text("""
                INSERT INTO messages VALUES
                (2, :tenant, 'Answer', 'pending',
                '{"status":"referenced","sources":[{"excerpt":"Historical source"}]}'),
                (3, :other, 'Private answer', 'sent', '{"sources":[]}')
            """),
                {"tenant": tenant, "other": other},
            )
            await conn.execute(text(f'CREATE ROLE "{role}"'))
            await conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
            await conn.execute(text(f'GRANT SELECT, UPDATE, DELETE ON messages TO "{role}"'))
        async with engine.begin() as conn:
            await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :id, true)"), {"id": str(tenant)}
            )
            assert list(
                (await conn.execute(text("SELECT id FROM messages ORDER BY id"))).scalars()
            ) == [1, 2]
            # Delivery retry changes only delivery status; the snapshot is not recomputed.
            for status in ("failed", "pending", "sent"):
                await conn.execute(
                    text("UPDATE messages SET delivery_status=:status WHERE id=2"),
                    {"status": status},
                )
            evidence = await conn.scalar(text("SELECT response_sources FROM messages WHERE id=2"))
            assert evidence["sources"][0]["excerpt"] == "Historical source"
            await conn.execute(text("DELETE FROM messages WHERE id=2"))
            assert (
                await conn.scalar(
                    text("SELECT count(*) FROM messages WHERE response_sources IS NOT NULL")
                )
                == 0
            )
        async with engine.begin() as conn:
            await conn.run_sync(lambda connection: migrate(connection, "downgrade"))
            assert await conn.scalar(text("SELECT content FROM messages WHERE id=1")) == "old"
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        await engine.dispose()
