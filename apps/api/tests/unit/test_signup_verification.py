import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import app.services.billing as billing
import app.services.signup_verification as verification
from app.models import PendingSignup
from app.schemas.signup import SignupCheckoutRequest


@pytest.fixture
def session():
    result = AsyncMock()
    result.add = MagicMock()
    return result


@pytest.fixture
def signup_body():
    return SignupCheckoutRequest(
        tenant_name="Escritório Teste",
        email="a@b.com",
        password="senha1234",
        credit_package_id=uuid.uuid4(),
    )


async def test_solicitacao_guarda_apenas_hash_e_envia_email(session, signup_body, monkeypatch):
    monkeypatch.setattr(verification.settings, "gmail_smtp_user", "sender@example.com")
    monkeypatch.setattr(verification.settings, "gmail_smtp_app_password", "test-secret")
    session.scalar.side_effect = [None, None]
    session.get.return_value = SimpleNamespace(active=True)
    sent = []

    async def fake_to_thread(fn, *args):
        sent.append(args)

    monkeypatch.setattr(verification.asyncio, "to_thread", fake_to_thread)
    await verification.request_verification(session, signup_body)

    pending = session.add.call_args.args[0]
    assert isinstance(pending, PendingSignup)
    assert pending.password_hash != "senha1234"
    assert pending.verification_token_hash == verification._digest(sent[0][1])
    assert sent[0][0] == "a@b.com"
    session.commit.assert_awaited_once()


async def test_confirmacao_consumida_e_checkout_exige_token_valido(session):
    token = "email-secret"
    pending = PendingSignup(
        email="a@b.com",
        tenant_name="Escritório",
        password_hash="hash",
        credit_package_id=uuid.uuid4(),
        verification_token_hash=verification._digest(token),
        verification_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    session.scalar.side_effect = [pending, pending, None]
    checkout_token = await verification.verify_email(session, token)
    assert pending.verification_token_hash is None
    assert pending.checkout_token_hash == verification._digest(checkout_token)
    assert await verification.get_verified_signup(session, checkout_token) is pending
    with pytest.raises(HTTPException) as exc:
        await verification.get_verified_signup(session, "outro-token")
    assert exc.value.status_code == 403


async def test_link_expirado_nao_confirma(session):
    session.scalar.return_value = PendingSignup(
        verification_expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    with pytest.raises(HTTPException) as exc:
        await verification.verify_email(session, "expired")
    assert exc.value.status_code == 400
    session.commit.assert_not_awaited()


async def test_falha_de_envio_libera_tentativa_imediata(session, signup_body, monkeypatch):
    monkeypatch.setattr(verification.settings, "gmail_smtp_user", "sender@example.com")
    monkeypatch.setattr(verification.settings, "gmail_smtp_app_password", "test-secret")
    session.scalar.side_effect = [None, None]
    session.get.return_value = SimpleNamespace(active=True)

    async def fail_to_thread(fn, *args):
        raise OSError("smtp indisponível")

    monkeypatch.setattr(verification.asyncio, "to_thread", fail_to_thread)
    with pytest.raises(HTTPException) as exc:
        await verification.request_verification(session, signup_body)

    pending = session.add.call_args.args[0]
    assert exc.value.status_code == 502
    assert pending.verification_token_hash is None
    assert pending.last_sent_at is None
    assert pending.send_count == 0
    assert session.commit.await_count == 2


async def test_checkout_confirmado_nao_envia_email_ou_senha_para_stripe(session, monkeypatch):
    pending_id = uuid.uuid4()
    pending = PendingSignup(
        id=pending_id,
        email="a@b.com",
        tenant_name="Escritório",
        password_hash="hash",
        credit_package_id=uuid.uuid4(),
        verified_at=datetime.now(UTC),
    )
    session.scalar.return_value = None
    session.get.return_value = SimpleNamespace(
        active=True, price_brl=Decimal("100.00"), name="Inicial"
    )
    sent = []

    def fake_create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(id="cs_123", url="https://checkout.stripe.com/pay/cs_123")

    monkeypatch.setattr(billing.stripe.checkout.Session, "create", fake_create)
    checkout_id, _url = await billing.create_verified_checkout_session(session, pending)
    assert checkout_id == "cs_123"
    assert sent[0]["metadata"] == {"pending_signup_id": str(pending_id)}
