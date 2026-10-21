import hashlib
import smtplib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tasks import team_emails as mail


def result(row):
    value = MagicMock()
    value.mappings.return_value.first.return_value = row
    return value


async def test_invitation_link_and_cancelled_invite(monkeypatch):
    token = "secret-token"
    monkeypatch.setattr(mail, "decrypt_access_token", lambda _: token)
    session = AsyncMock()
    session.execute.side_effect = [
        result({"name": "Office", "status": "active"}),
        result(
            {
                "email": "lawyer@example.com",
                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            }
        ),
    ]
    job = dict(
        tenant_id=uuid.uuid4(),
        kind="invitation",
        invitation_id=uuid.uuid4(),
        secret_encrypted="encrypted",
    )
    recipient, _, body = await mail.render_email(session, job)
    assert recipient == "lawyer@example.com"
    assert "/convite#token=secret-token" in body
    session.execute.side_effect = [result({"name": "Office", "status": "active"}), result(None)]
    assert await mail.render_email(session, job) is None


async def test_urgency_email_contains_link_but_no_client_content():
    session = AsyncMock()
    session.execute.side_effect = [
        result({"name": "Office", "status": "active"}),
        result(
            {
                "email": "lawyer@example.com",
                "agent_name": "Consumer",
                "urgent_since": datetime.now(UTC),
            }
        ),
    ]
    job = dict(
        tenant_id=uuid.uuid4(),
        kind="urgency",
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        episode_id=uuid.uuid4(),
    )
    _, _, body = await mail.render_email(session, job)
    assert f"/escritorios?escritorio={job['tenant_id']}&conversa={job['conversation_id']}" in body
    assert "A IA continua atendendo" in body
    session.execute.side_effect = [result({"name": "Office", "status": "active"}), result(None)]
    assert await mail.render_email(session, job) is None


def credit_job(kind):
    return dict(tenant_id=uuid.uuid4(), kind=kind, user_id=uuid.uuid4(), episode_id=uuid.uuid4())


@pytest.mark.parametrize(
    "kind,balance,subject",
    [
        ("low_credits", 150.5, "Advoxs — seus créditos estão acabando"),
        ("credits_depleted", 0, "Advoxs — seus créditos acabaram"),
    ],
)
async def test_credit_alert_links_to_purchase_page(kind, balance, subject):
    session = AsyncMock()
    session.execute.side_effect = [
        result({"name": "Office", "status": "active"}),
        result({"email": "admin@example.com", "credit_balance": balance, "threshold": 200}),
    ]
    job = credit_job(kind)
    recipient, title, body = await mail.render_email(session, job)
    assert recipient == "admin@example.com"
    assert title == subject
    assert f"/escritorios?escritorio={job['tenant_id']}&destino=creditos" in body
    if kind == "low_credits":
        assert "150,5 créditos restantes" in body


@pytest.mark.parametrize(
    "kind,balance",
    [
        ("low_credits", 500),  # recharged above the threshold before sending
        ("low_credits", 0),  # already depleted: the depleted e-mail covers it
        ("credits_depleted", 40),  # recharged before sending
    ],
)
async def test_credit_alert_cancelled_when_balance_changed(kind, balance):
    session = AsyncMock()
    session.execute.side_effect = [
        result({"name": "Office", "status": "active"}),
        result({"email": "admin@example.com", "credit_balance": balance, "threshold": 200}),
    ]
    assert await mail.render_email(session, credit_job(kind)) is None


async def test_credit_alert_cancelled_when_recipient_is_no_longer_admin():
    session = AsyncMock()
    session.execute.side_effect = [result({"name": "Office", "status": "active"}), result(None)]
    assert await mail.render_email(session, credit_job("low_credits")) is None


@pytest.mark.parametrize(
    "exception,attempts,expected",
    [
        (None, 1, "sent"),
        (ConnectionError("temporary"), 1, "pending"),
        (ConnectionError("temporary"), 5, "failed"),
        (smtplib.SMTPAuthenticationError(535, b"credentials"), 1, "failed"),
    ],
)
async def test_delivery_retry_and_lease_updates(monkeypatch, exception, attempts, expected):
    session = AsyncMock()
    job = dict(id=uuid.uuid4(), attempts=attempts)
    session.execute.side_effect = [result(job), result(None), result(None)]
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(
        mail, "render_email", AsyncMock(return_value=("a@example.com", "Title", "Body"))
    )
    sender = MagicMock(side_effect=exception)
    monkeypatch.setattr(mail, "send_email", sender)
    await mail.deliver_team_emails({"system_session_factory": factory})
    update = session.execute.call_args_list[1]
    assert update.args[1]["state"] == expected
    assert update.args[1]["id"] == job["id"]
    assert "claim_id=:claim" in str(update.args[0])
    assert "SKIP LOCKED" in str(session.execute.call_args_list[0].args[0])


async def test_obsolete_notification_is_cancelled_without_sending(monkeypatch):
    session = AsyncMock()
    session.execute.side_effect = [
        result(dict(id=uuid.uuid4(), attempts=1)),
        result(None),
        result(None),
    ]
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(mail, "render_email", AsyncMock(return_value=None))
    sender = MagicMock()
    monkeypatch.setattr(mail, "send_email", sender)
    await mail.deliver_team_emails({"system_session_factory": factory})
    sender.assert_not_called()
    assert session.execute.call_args_list[1].args[1]["state"] == "cancelled"
