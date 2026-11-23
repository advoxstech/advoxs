"""Real triggers + real notification outbox, with SMTP replaced by a mock."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from app.tasks import team_emails
from tests.integration.test_team_emails_postgres import database  # noqa: F401


@pytest.fixture
async def human_db(database):  # noqa: F811
    engine, ctx = database
    async with engine.begin() as conn:
        # Extend the existing pre-team schema fixture, then run the actual migration.
        await conn.execute(text("DELETE FROM team_emails"))
        await conn.execute(text("UPDATE conversations SET urgent_since=NULL"))
        await conn.execute(
            text(
                "ALTER TABLE messages ADD content TEXT, ADD sender_type TEXT, "
                "ADD tenant_id UUID, ADD conversation_id UUID"
            )
        )
        migration = (
            Path(__file__).parents[3] / "api/alembic/versions/0057_human_attendance_requests.sql"
        )
        for statement in migration.read_text().split("\n-- statement\n"):
            await conn.execute(text(statement))
        await conn.execute(
            text("INSERT INTO agents SELECT gen_random_uuid(),id,'Consumer',true FROM tenants")
        )
        await conn.execute(
            text("UPDATE conversations SET current_agent_id=(SELECT id FROM agents)")
        )
    return engine, ctx


async def send_contact(engine, message):
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO messages(id,tenant_id,conversation_id,sender_type,content) "
                "SELECT gen_random_uuid(),tenant_id,id,'contact',:message "
                "FROM conversations"
            ),
            {"message": message},
        )


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Quero falar com um advogado humano", True),
        ("Olá, gostaria de conversar com uma pessoa", True),
        ("Preciso de atendimento humano", True),
        ("Pode me passar para um advogado?", True),
        ("Não quero robô, quero falar com uma pessoa", True),
        ("Não quero falar com um humano", False),
        ("Quero saber se você é humano ou IA", False),
        ("Ela disse que quer falar com um advogado", False),
        ("Se eu quiser falar com um advogado, como funciona?", False),
        ("Quero saber meus direitos como consumidor", False),
    ],
)
async def test_direct_request_detection(human_db, message, expected):
    engine, _ = human_db
    async with engine.begin() as conn:
        assert (
            await conn.scalar(
                text("SELECT requests_human_attendance(:message)"), {"message": message}
            )
            is expected
        )


async def test_request_dedup_audit_and_email_without_pausing(human_db, monkeypatch):
    engine, ctx = human_db
    sender = MagicMock()
    monkeypatch.setattr(team_emails, "send_email", sender)
    await send_contact(engine, "Quero falar com um advogado humano")
    await send_contact(engine, "Preciso de atendimento humano")
    async with engine.begin() as conn:
        row = (await conn.execute(text("SELECT * FROM conversations"))).mappings().one()
        assert row["state"] == "agent"
        assert row["urgent_since"] is None
        assert row["human_request_agent_id"] == row["current_agent_id"]
        assert await conn.scalar(text("SELECT count(*) FROM team_emails")) == 1
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM attendance_events WHERE action='human_requested'")
            )
            == 1
        )
    await asyncio.gather(team_emails.deliver_team_emails(ctx), team_emails.deliver_team_emails(ctx))
    sender.assert_called_once()
    assert "pedido de atendimento humano" in sender.call_args.args[1]


async def test_tests_never_notify_real_team(human_db, monkeypatch):
    engine, ctx = human_db
    sender = MagicMock()
    monkeypatch.setattr(team_emails, "send_email", sender)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE conversations SET is_test=true"))
    await send_contact(engine, "Quero atendimento humano")
    async with engine.begin() as conn:
        assert await conn.scalar(text("SELECT human_requested_at IS NOT NULL FROM conversations"))
        assert await conn.scalar(text("SELECT count(*) FROM team_emails")) == 0
    await team_emails.deliver_team_emails(ctx)
    sender.assert_not_called()


async def test_assuming_clears_request_and_cancels_stale_notification(human_db, monkeypatch):
    engine, ctx = human_db
    sender = MagicMock()
    monkeypatch.setattr(team_emails, "send_email", sender)
    await send_contact(engine, "Quero falar com um advogado humano")
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE conversations SET state='human'"))
        assert await conn.scalar(text("SELECT human_requested_at FROM conversations")) is None
    await team_emails.deliver_team_emails(ctx)
    sender.assert_not_called()
    async with engine.begin() as conn:
        assert await conn.scalar(text("SELECT status FROM team_emails")) == "cancelled"


async def test_deactivated_recipient_is_rechecked_before_delivery(human_db, monkeypatch):
    engine, ctx = human_db
    sender = MagicMock()
    monkeypatch.setattr(team_emails, "send_email", sender)
    await send_contact(engine, "Preciso de atendimento humano")
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE office_memberships SET active=false"))
    await team_emails.deliver_team_emails(ctx)
    sender.assert_not_called()


async def test_linked_lawyer_is_notified_instead_of_admin(human_db, monkeypatch):
    engine, ctx = human_db
    sender = MagicMock()
    monkeypatch.setattr(team_emails, "send_email", sender)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users(id,tenant_id,role,email) "
                "SELECT gen_random_uuid(),id,'lawyer','lawyer@example.com' FROM tenants"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO agent_lawyers(tenant_id,agent_id,user_id) "
                "SELECT a.tenant_id,a.id,u.id FROM agents a JOIN users u "
                "ON u.tenant_id=a.tenant_id WHERE u.role='lawyer'"
            )
        )
    await send_contact(engine, "Quero falar com um advogado humano")
    await team_emails.deliver_team_emails(ctx)
    sender.assert_called_once()
    assert sender.call_args.args[0] == "lawyer@example.com"


async def test_pending_request_keeps_original_specialist(human_db):
    engine, _ = human_db
    await send_contact(engine, "Preciso de atendimento humano")
    async with engine.begin() as conn:
        original = await conn.scalar(text("SELECT current_agent_id FROM conversations"))
        new_agent = await conn.scalar(
            text(
                "INSERT INTO agents VALUES "
                "(gen_random_uuid(),(SELECT id FROM tenants),'Reception',false) RETURNING id"
            )
        )
        await conn.execute(
            text("UPDATE conversations SET current_agent_id=:agent"), {"agent": new_agent}
        )
        assert await conn.scalar(text("SELECT current_agent_id FROM conversations")) == original
