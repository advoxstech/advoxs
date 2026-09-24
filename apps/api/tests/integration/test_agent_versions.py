"""Real PostgreSQL migration, RLS and competing publications.

Set AGENT_VERSIONS_TEST_DATABASE_URL to a disposable PostgreSQL database.
Each test uses and removes its own schema; no application database is selected by default.
"""

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from app.api.deps import TenantContext
from app.api.v1.agents import publish_version, restore_version, save_draft
from app.models import Agent, AgentVersion, Conversation
from app.schemas.agents import AgentDraftIn, AgentPublishIn, DraftRevisionIn

_PATH = Path(__file__).parents[2] / "alembic/versions/0038_agent_versions.py"
_SPEC = importlib.util.spec_from_file_location("agent_versions_migration", _PATH)
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


def migrate(connection, direction="upgrade"):
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration, direction)()


@pytest.fixture
async def database():
    url = os.getenv("AGENT_VERSIONS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires an explicitly configured disposable PostgreSQL database")
    schema = f"agent_versions_test_{uuid.uuid4().hex}"
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    tenant_id, agent_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    role = f"agent_versions_reader_{uuid.uuid4().hex}"
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text("CREATE TABLE tenants (id UUID PRIMARY KEY)"))
            await conn.execute(
                text(
                    "CREATE TABLE users (id UUID PRIMARY KEY, "
                    "tenant_id UUID REFERENCES tenants, name TEXT)"
                )
            )
            await conn.execute(
                text("""
                CREATE TABLE agents (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id UUID NOT NULL REFERENCES tenants,
                    name TEXT NOT NULL, instructions TEXT NOT NULL,
                    is_entry_point BOOLEAN NOT NULL DEFAULT false,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            )
            await conn.execute(CreateTable(Conversation.__table__))
            await conn.execute(text("INSERT INTO tenants VALUES (:id)"), {"id": tenant_id})
            await conn.execute(
                text("INSERT INTO users VALUES (:id, :tenant, 'Ana')"),
                {"id": user_id, "tenant": tenant_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO agents (id, tenant_id, name, instructions) "
                    "VALUES (:id, :tenant, 'Original', 'Texto original')"
                ),
                {"id": agent_id, "tenant": tenant_id},
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
        yield engine, tenant_id, agent_id, user_id, role
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        await engine.dispose()


async def test_migration_backfill_trigger_rls_and_downgrade(database):
    engine, tenant_id, agent_id, _, role = database
    async with engine.begin() as conn:
        first = (
            await conn.execute(
                text(
                    "SELECT number, instructions, author_id FROM agent_versions WHERE agent_id=:id"
                ),
                {"id": agent_id},
            )
        ).one()
        assert tuple(first) == (1, "Texto original", None)
        # A newly provisioned agent gets v1 even outside the CRUD route.
        await conn.execute(
            text(
                "INSERT INTO agents (tenant_id, name, instructions) "
                "VALUES (:id, 'Novo', 'Novo texto')"
            ),
            {"id": tenant_id},
        )
        assert await conn.scalar(text("SELECT count(*) FROM agent_versions")) == 2
        conversation_id = await conn.scalar(
            text(
                "INSERT INTO conversations (tenant_id, contact_phone_number, is_test) "
                "VALUES (:tenant, 'rascunho-isolado', true) RETURNING id"
            ),
            {"tenant": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO agent_test_sessions "
                "(conversation_id, tenant_id, agent_id, draft_revision, agents_snapshot) "
                "VALUES (:conversation, :tenant, :agent, 0, '[]'::jsonb)"
            ),
            {"conversation": conversation_id, "tenant": tenant_id, "agent": agent_id},
        )
        await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :id, true)"), {"id": str(uuid.uuid4())}
        )
        assert await conn.scalar(text("SELECT count(*) FROM agent_versions")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM agent_test_sessions")) == 0
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :id, true)"), {"id": str(tenant_id)}
        )
        assert await conn.scalar(text("SELECT count(*) FROM agent_versions")) == 2
        assert await conn.scalar(text("SELECT count(*) FROM agent_test_sessions")) == 1
        # The trigger also works as an application role with RLS enabled on the history.
        await conn.execute(
            text(
                "INSERT INTO agents (tenant_id, name, instructions) "
                "VALUES (:tenant, 'Agente da aplicação', 'Instruções')"
            ),
            {"tenant": tenant_id},
        )
        assert await conn.scalar(text("SELECT count(*) FROM agent_versions")) == 3
        await conn.execute(text("RESET ROLE"))
        await conn.run_sync(lambda connection: migrate(connection, "downgrade"))
        assert (
            await conn.scalar(
                text("SELECT instructions FROM agents WHERE id=:id"), {"id": agent_id}
            )
            == "Texto original"
        )
        await conn.run_sync(migrate)
        assert await conn.scalar(text("SELECT count(*) FROM agent_versions")) == 3


async def test_drafts_concurrent_publication_restore_and_rollback(database):
    engine, tenant_id, agent_id, user_id, _ = database
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ctx = TenantContext(tenant_id=tenant_id, user_id=user_id, role="admin")
    async with sessions() as session:
        await save_draft(
            agent_id,
            AgentDraftIn(name="Novo", instructions="Novas instruções", expected_revision=0),
            ctx,
            session,
        )
    async with sessions() as session:
        assert (await session.get(Agent, agent_id)).instructions == "Texto original"

    async def publish():
        async with sessions() as session:
            try:
                result = await publish_version(
                    agent_id, AgentPublishIn(expected_revision=1), ctx, session
                )
                return result.published_version
            except HTTPException as error:
                return error.status_code

    assert sorted(await asyncio.gather(publish(), publish())) == [2, 409]
    async with sessions() as session:
        assert len((await session.scalars(select(AgentVersion))).all()) == 2
        await restore_version(agent_id, 1, DraftRevisionIn(expected_revision=2), ctx, session)
        assert (await session.get(Agent, agent_id)).instructions == "Novas instruções"
        result = await publish_version(agent_id, AgentPublishIn(expected_revision=3), ctx, session)
        assert result.published_version == 3
        assert result.published.instructions == "Texto original"
        await save_draft(
            agent_id,
            AgentDraftIn(name="Falha", instructions="Não publicar", expected_revision=4),
            ctx,
            session,
        )
    # Force the version insert to fail (invalid author FK): both live update and history roll back.
    invalid_ctx = ctx.model_copy(update={"user_id": uuid.uuid4()})
    async with sessions() as session:
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await publish_version(
                agent_id, AgentPublishIn(expected_revision=5), invalid_ctx, session
            )
        await session.rollback()
    async with sessions() as session:
        agent = await session.get(Agent, agent_id)
        assert agent.published_version == 3
        assert agent.instructions == "Texto original"
        assert len((await session.scalars(select(AgentVersion))).all()) == 3
