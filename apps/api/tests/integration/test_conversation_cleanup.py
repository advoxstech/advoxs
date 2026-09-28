"""Migration real da exclusão durável, FKs de auditoria e isolamento RLS."""

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_PATH = Path(__file__).parents[2] / "alembic/versions/0040_durable_conversation_cleanup.py"
_SPEC = importlib.util.spec_from_file_location("conversation_cleanup_migration", _PATH)
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


def migrate(connection) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()


@pytest.fixture
async def database():
    url = os.getenv("AGENT_VERSIONS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires an explicitly configured disposable PostgreSQL database")
    schema = f"conversation_cleanup_test_{uuid.uuid4().hex}"
    role = f"conversation_cleanup_reader_{uuid.uuid4().hex}"
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text("CREATE TABLE tenants (id UUID PRIMARY KEY)"))
            await conn.execute(
                text(
                    "CREATE TABLE conversations ("
                    "id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES tenants, "
                    "contact_phone_number TEXT NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE messages ("
                    "id UUID PRIMARY KEY, conversation_id UUID NOT NULL REFERENCES conversations)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE usage_records ("
                    "id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES tenants, "
                    "conversation_id UUID NOT NULL, related_message_id UUID NOT NULL UNIQUE, "
                    "CONSTRAINT fk_usage_records_conversation_id_conversations "
                    "FOREIGN KEY (conversation_id) REFERENCES conversations, "
                    "CONSTRAINT fk_usage_records_related_message_id_messages "
                    "FOREIGN KEY (related_message_id) REFERENCES messages)"
                )
            )
            await conn.run_sync(migrate)
            await conn.execute(text(f'CREATE ROLE "{role}"'))
            await conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
            await conn.execute(
                text(
                    f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" '
                    f'TO "{role}"'
                )
            )
        yield engine, role
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        await engine.dispose()


async def test_migration_preserva_auditoria_e_isola_jobs_por_tenant(database) -> None:
    engine, role = database
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    conversation_a, conversation_b = uuid.uuid4(), uuid.uuid4()
    message_a = uuid.uuid4()
    usage_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO tenants VALUES (:a), (:b)"), {"a": tenant_a, "b": tenant_b}
        )
        await conn.execute(
            text(
                "INSERT INTO conversations (id, tenant_id, contact_phone_number) "
                "VALUES (:ca, :ta, '5511'), (:cb, :tb, '5522')"
            ),
            {"ca": conversation_a, "ta": tenant_a, "cb": conversation_b, "tb": tenant_b},
        )
        await conn.execute(
            text("INSERT INTO messages VALUES (:id, :conversation)"),
            {"id": message_a, "conversation": conversation_a},
        )
        await conn.execute(
            text(
                "INSERT INTO usage_records "
                "(id, tenant_id, conversation_id, related_message_id) "
                "VALUES (:id, :tenant, :conversation, :message)"
            ),
            {
                "id": usage_id,
                "tenant": tenant_a,
                "conversation": conversation_a,
                "message": message_a,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO conversation_cleanup_jobs "
                "(tenant_id, conversation_id) VALUES (:ta, :ca), (:tb, :cb)"
            ),
            {"ta": tenant_a, "ca": conversation_a, "tb": tenant_b, "cb": conversation_b},
        )

        await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :tenant, true)"),
            {"tenant": str(tenant_a)},
        )
        assert await conn.scalar(text("SELECT count(*) FROM conversation_cleanup_jobs")) == 1
        await conn.execute(text("RESET ROLE"))

        await conn.execute(text("DELETE FROM messages WHERE id=:id"), {"id": message_a})
        assert (
            await conn.scalar(
                text("SELECT related_message_id FROM usage_records WHERE id=:id"), {"id": usage_id}
            )
            is None
        )
        await conn.execute(text("DELETE FROM conversations WHERE id=:id"), {"id": conversation_a})
        assert (
            await conn.scalar(
                text("SELECT conversation_id FROM usage_records WHERE id=:id"), {"id": usage_id}
            )
            is None
        )
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM conversation_cleanup_jobs WHERE conversation_id=:id"),
                {"id": conversation_a},
            )
            == 0
        )
