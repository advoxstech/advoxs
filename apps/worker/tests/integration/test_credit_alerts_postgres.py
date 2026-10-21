"""Credit alert trigger (migration 0047) and delivery, on real PostgreSQL."""

import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.tasks import team_emails

VERSIONS = Path(__file__).parents[3] / "api/alembic/versions"


@pytest.fixture
async def database():
    url = os.getenv("CONVERSATION_CLEANUP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires explicitly configured disposable PostgreSQL")
    schema = "credit_alert_test_" + uuid.uuid4().hex
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    ids = {k: uuid.uuid4() for k in ("t", "admin", "admin2", "lawyer")}
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            for ddl in (
                "CREATE TABLE tenants(id UUID PRIMARY KEY,name TEXT,status TEXT,"
                "credit_balance NUMERIC(12,4) NOT NULL DEFAULT 0)",
                "CREATE TABLE users(id UUID PRIMARY KEY,tenant_id UUID,role TEXT,email TEXT,"
                "session_version INTEGER NOT NULL DEFAULT 0)",
                "CREATE TABLE agents(id UUID PRIMARY KEY,tenant_id UUID,"
                "name TEXT,is_entry_point BOOL)",
                "CREATE TABLE conversations(id UUID PRIMARY KEY,tenant_id UUID,"
                "current_agent_id UUID,urgent_agent_id UUID,urgent_since TIMESTAMPTZ,"
                "is_test BOOL DEFAULT false,deletion_requested_at TIMESTAMPTZ,state TEXT)",
                "CREATE TABLE messages(id UUID PRIMARY KEY)",
                "CREATE TABLE credit_transactions(id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
                "tenant_id UUID,type TEXT,amount_credits NUMERIC(12,4),"
                "created_at TIMESTAMPTZ DEFAULT now())",
            ):
                await conn.execute(text(ddl))
            for name in ("0045_office_team", "0046_multiple_offices", "0047_credit_alert_emails"):
                sql = (VERSIONS / f"{name}.sql").read_text()
                for statement in sql.split("\n-- statement\n"):
                    await conn.execute(text(statement))
            await conn.execute(text("INSERT INTO tenants VALUES (:t,'Office','active',1000)"), ids)
            for key, role in (("admin", "admin"), ("admin2", "admin"), ("lawyer", "lawyer")):
                await conn.execute(
                    text("INSERT INTO users(id,tenant_id,role,email) VALUES (:id,:t,:role,:e)"),
                    dict(id=ids[key], t=ids["t"], role=role, e=f"{key}@example.com"),
                )
            # Latest purchase of 1000 credits -> low-credit threshold of 200.
            await conn.execute(
                text(
                    "INSERT INTO credit_transactions(tenant_id,type,amount_credits,created_at)"
                    " VALUES (:t,'purchase',5000,now()-interval '1 day'),"
                    "(:t,'purchase',1000,now())"
                ),
                ids,
            )
        yield engine, ids
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def set_balance(engine, ids, value):
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET credit_balance=:v WHERE id=:t"), dict(v=value, t=ids["t"])
        )


async def alerts(engine):
    async with engine.begin() as conn:
        rows = await conn.execute(
            text("SELECT kind,user_id,episode_id FROM team_emails ORDER BY kind,user_id")
        )
        return rows.all()


async def test_crossing_threshold_queues_one_alert_per_admin(database):
    engine, ids = database
    await set_balance(engine, ids, 500)
    assert await alerts(engine) == []

    await set_balance(engine, ids, 199)
    rows = await alerts(engine)
    assert {r.kind for r in rows} == {"low_credits"}
    assert {r.user_id for r in rows} == {ids["admin"], ids["admin2"]}  # lawyer excluded
    assert len({r.episode_id for r in rows}) == 1

    await set_balance(engine, ids, 120)  # still below: no new alert
    assert len(await alerts(engine)) == 2


async def test_depletion_and_rearm_after_purchase(database):
    engine, ids = database
    await set_balance(engine, ids, 150)
    await set_balance(engine, ids, -2)
    kinds = [r.kind for r in await alerts(engine)]
    assert kinds.count("low_credits") == 2 and kinds.count("credits_depleted") == 2

    await set_balance(engine, ids, 1000)  # purchase: increases never alert
    assert len(await alerts(engine)) == 4
    await set_balance(engine, ids, 100)
    assert [r.kind for r in await alerts(engine)].count("low_credits") == 4


async def test_large_debit_straight_to_zero_sends_only_depleted(database):
    engine, ids = database
    await set_balance(engine, ids, 0)
    assert {r.kind for r in await alerts(engine)} == {"credits_depleted"}


async def test_delivery_sends_to_admins_and_cancels_after_recharge(database, monkeypatch):
    engine, ids = database
    sender = MagicMock()
    monkeypatch.setattr(team_emails, "send_email", sender)
    ctx = {"system_session_factory": async_sessionmaker(engine)}

    await set_balance(engine, ids, 150)
    await team_emails.deliver_team_emails(ctx)
    assert sorted(call.args[0] for call in sender.call_args_list) == [
        "admin2@example.com",
        "admin@example.com",
    ]
    assert "150 créditos restantes" in sender.call_args_list[0].args[2]

    sender.reset_mock()
    await set_balance(engine, ids, 0)
    await set_balance(engine, ids, 800)  # recharged before the worker ran
    await team_emails.deliver_team_emails(ctx)
    sender.assert_not_called()
    async with engine.begin() as conn:
        statuses = await conn.scalars(
            text("SELECT status FROM team_emails WHERE kind='credits_depleted'")
        )
        assert set(statuses) == {"cancelled"}
