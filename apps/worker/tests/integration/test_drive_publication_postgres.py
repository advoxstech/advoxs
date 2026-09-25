"""Opt-in: TEST_DRIVE_DATABASE_URL deve apontar para um Postgres descartável.

Não usa DATABASE_URL da aplicação. Cria um schema e papel temporários exclusivos,
aplica a migration real 0040 e exercita publicação/rollback com RLS real.
"""

import importlib.util
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.tasks.knowledge_base import _publish_replacement


@pytest.fixture
async def database():
    url = os.getenv("TEST_DRIVE_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DRIVE_DATABASE_URL não configurada (Postgres descartável)")
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    name = "drive_test_" + uuid.uuid4().hex[:12]
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": name}})
    role_engine = create_async_engine(
        url,
        connect_args={
            "server_settings": {
                "search_path": name,
                "role": name,
            }
        },
    )
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{name}"'))
        await conn.execute(text(f'CREATE ROLE "{name}"'))
    tid, other, aid, old, new = (uuid.uuid4() for _ in range(5))
    try:
        async with engine.begin() as conn:
            for statement in (
                "CREATE TABLE tenants (id uuid PRIMARY KEY)",
                "CREATE TABLE agents (id uuid PRIMARY KEY, tenant_id uuid REFERENCES tenants)",
                """CREATE TABLE knowledge_base_files (
                    id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants,
                    filename text NOT NULL, size_bytes bigint NOT NULL, mime_type text NOT NULL,
                    status text NOT NULL, error_message text, category text,
                    uploaded_at timestamptz NOT NULL DEFAULT now(),
                    CONSTRAINT uq_knowledge_base_files_tenant_filename UNIQUE (tenant_id, filename)
                )""",
                """CREATE TABLE agent_knowledge_base_files (
                    agent_id uuid REFERENCES agents ON DELETE CASCADE,
                    knowledge_base_file_id uuid REFERENCES knowledge_base_files ON DELETE CASCADE,
                    PRIMARY KEY (agent_id, knowledge_base_file_id))""",
            ):
                await conn.execute(text(statement))
            migration_path = (
                Path(__file__).resolve().parents[3]
                / "api/alembic/versions/0040_google_drive_imports.py"
            )
            spec = importlib.util.spec_from_file_location("drive_migration", migration_path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)

            def upgrade(sync_conn):
                with Operations.context(MigrationContext.configure(sync_conn)):
                    migration.upgrade()

            await conn.run_sync(upgrade)
            await conn.execute(text("ALTER TABLE knowledge_base_files ENABLE ROW LEVEL SECURITY"))
            await conn.execute(
                text("""CREATE POLICY tenant_policy ON knowledge_base_files
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                """)
            )
            await conn.execute(text(f'GRANT USAGE ON SCHEMA "{name}" TO "{name}"'))
            await conn.execute(
                text(
                    f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{name}" '
                    f'TO "{name}"'
                )
            )
            await conn.execute(
                text("INSERT INTO tenants VALUES (:tid), (:other)"), {"tid": tid, "other": other}
            )
            await conn.execute(
                text("INSERT INTO agents VALUES (:aid, :tid)"), {"aid": aid, "tid": tid}
            )
            await conn.execute(
                text("""INSERT INTO knowledge_base_files
                (id, tenant_id, filename, size_bytes, mime_type, status, category, drive_file_id)
                VALUES (:old, :tid, 'regras.pdf', 10, 'application/pdf',
                        'ready', 'livros_digitais', 'drive-id')"""),
                {"old": old, "tid": tid},
            )
            await conn.execute(
                text("""INSERT INTO knowledge_base_files
                (id, tenant_id, filename, size_bytes, mime_type,
                 status, drive_file_id, replaces_file_id)
                VALUES (:new, :tid, 'regras.pdf', 12, 'application/pdf',
                        'processing', 'drive-id', :old)"""),
                {"old": old, "tid": tid, "new": new},
            )
            await conn.execute(
                text("INSERT INTO agent_knowledge_base_files VALUES (:aid, :old)"),
                {"aid": aid, "old": old},
            )
        yield SimpleNamespace(
            engine=engine,
            factory=async_sessionmaker(role_engine, expire_on_commit=False),
            tid=tid,
            other=other,
            aid=aid,
            old=old,
            new=new,
        )
    finally:
        await role_engine.dispose()
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
            await conn.execute(text(f'DROP ROLE "{name}"'))
        await admin.dispose()


async def test_migration_and_publication_preserve_original_and_switch_all_links(database):
    db = database
    await _publish_replacement(db.factory, str(db.tid), str(db.new), db.old)
    async with db.engine.connect() as conn:
        old = (
            await conn.execute(
                text("SELECT * FROM knowledge_base_files WHERE id=:id"), {"id": db.old}
            )
        ).one()
        new = (
            await conn.execute(
                text("SELECT * FROM knowledge_base_files WHERE id=:id"), {"id": db.new}
            )
        ).one()
        assert old.superseded_at is not None and old.status == "ready"
        assert new.status == "ready" and new.replaces_file_id is None
        assert new.category == "livros_digitais"
        assert (
            await conn.execute(
                text("SELECT knowledge_base_file_id FROM agent_knowledge_base_files")
            )
        ).scalar_one() == db.new
    # Repetição após sucesso não publica outra versão nem apaga o original.
    await _publish_replacement(db.factory, str(db.tid), str(db.new), db.old)


async def test_publication_transaction_rolls_back_old_version_if_switch_fails(database):
    db = database
    async with db.engine.begin() as conn:
        await conn.execute(
            text("""CREATE FUNCTION reject_link_change() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'simulated link failure'; END $$""")
        )
        await conn.execute(
            text("""CREATE TRIGGER reject_link_change BEFORE UPDATE ON
            agent_knowledge_base_files FOR EACH ROW EXECUTE FUNCTION reject_link_change()""")
        )
    with pytest.raises(Exception, match="simulated link failure"):
        await _publish_replacement(db.factory, str(db.tid), str(db.new), db.old)
    async with db.engine.connect() as conn:
        assert (
            await conn.execute(
                text("SELECT superseded_at FROM knowledge_base_files WHERE id=:id"), {"id": db.old}
            )
        ).scalar_one() is None
        assert (
            await conn.execute(
                text("SELECT status FROM knowledge_base_files WHERE id=:id"), {"id": db.new}
            )
        ).scalar_one() == "processing"
        assert (
            await conn.execute(
                text("SELECT knowledge_base_file_id FROM agent_knowledge_base_files")
            )
        ).scalar_one() == db.old


async def test_wrong_tenant_cannot_publish_other_offices_document(database):
    db = database
    await _publish_replacement(db.factory, str(db.other), str(db.new), db.old)
    async with db.engine.connect() as conn:
        assert (
            await conn.execute(
                text("SELECT status FROM knowledge_base_files WHERE id=:id"), {"id": db.new}
            )
        ).scalar_one() == "processing"
        assert (
            await conn.execute(
                text("SELECT knowledge_base_file_id FROM agent_knowledge_base_files")
            )
        ).scalar_one() == db.old


async def test_duplicate_active_drive_file_rejected_but_other_office_allowed(database):
    db = database
    statement = text("""INSERT INTO knowledge_base_files
        (id, tenant_id, filename, size_bytes, mime_type, status, drive_file_id)
        VALUES (:id, :tid, 'outro.pdf', 1, 'application/pdf', 'processing', 'drive-id')""")
    with pytest.raises(Exception, match="uq_kb_drive_active"):
        async with db.engine.begin() as conn:
            await conn.execute(statement, {"id": uuid.uuid4(), "tid": db.tid})
    async with db.engine.begin() as conn:
        await conn.execute(statement, {"id": uuid.uuid4(), "tid": db.other})
