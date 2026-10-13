"""Outbox competing workers and current authorization, using the real migration."""

import asyncio
import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.tasks import team_emails


@pytest.fixture
async def database():
    url = os.getenv("CONVERSATION_CLEANUP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires explicitly configured disposable PostgreSQL")
    schema = "team_mail_test_" + uuid.uuid4().hex
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            for ddl in (
                "CREATE TABLE tenants(id UUID PRIMARY KEY,name TEXT,status TEXT)",
                # Match the users schema after migration 0037, before applying 0045/0046.
                "CREATE TABLE users(id UUID PRIMARY KEY,tenant_id UUID,role TEXT,email TEXT,"
                "session_version INTEGER NOT NULL DEFAULT 0)",
                "CREATE TABLE agents(id UUID PRIMARY KEY,tenant_id UUID,"
                "name TEXT,is_entry_point BOOL)",
                "CREATE TABLE conversations(id UUID PRIMARY KEY,tenant_id UUID,"
                "current_agent_id UUID,urgent_agent_id UUID,urgent_since TIMESTAMPTZ,"
                "is_test BOOL DEFAULT false,deletion_requested_at TIMESTAMPTZ,state TEXT)",
                "CREATE TABLE messages(id UUID PRIMARY KEY)",
            ):
                await conn.execute(text(ddl))
            migration = Path(__file__).parents[3] / "api/alembic/versions/0045_office_team.sql"
            for statement in migration.read_text().split("\n-- statement\n"):
                await conn.execute(text(statement))
            for statement in (
                (migration.parent / "0046_multiple_offices.sql")
                .read_text()
                .split("\n-- statement\n")
            ):
                await conn.execute(text(statement))
            await conn.execute(
                text("INSERT INTO tenants VALUES (gen_random_uuid(),'Office','active')")
            )
            await conn.execute(
                text(
                    "INSERT INTO users(id,tenant_id,role,email)"
                    " SELECT gen_random_uuid(),id,'admin','a@example.com' FROM tenants"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO conversations(id,tenant_id,state)"
                    " SELECT gen_random_uuid(),id,'agent' FROM tenants"
                )
            )
            await conn.execute(text("UPDATE conversations SET urgent_since=now()"))
        yield engine, {"system_session_factory": async_sessionmaker(engine)}
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def test_concurrent_delivery_sends_one_email_without_pausing_ai(database, monkeypatch):
    engine, ctx = database
    sender = MagicMock()
    monkeypatch.setattr(team_emails, "send_email", sender)
    await asyncio.gather(team_emails.deliver_team_emails(ctx), team_emails.deliver_team_emails(ctx))
    sender.assert_called_once()
    async with engine.begin() as conn:
        assert await conn.scalar(text("SELECT status FROM team_emails")) == "sent"
        assert await conn.scalar(text("SELECT state FROM conversations")) == "agent"


async def test_stale_lease_rechecks_deactivated_recipient(database, monkeypatch):
    engine, ctx = database
    sender = MagicMock()
    monkeypatch.setattr(team_emails, "send_email", sender)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE team_emails SET status='processing',locked_at=now()-interval '10 minutes'")
        )
        await conn.execute(text("UPDATE users SET active=false"))
    await team_emails.deliver_team_emails(ctx)
    sender.assert_not_called()
    async with engine.begin() as conn:
        assert await conn.scalar(text("SELECT status FROM team_emails")) == "cancelled"
