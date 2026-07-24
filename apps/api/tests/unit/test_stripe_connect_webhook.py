import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.webhooks.stripe_connect as webhook_module
from app.api.v1.webhooks.stripe_connect import _resolve_account_status
from app.core.db import get_system_session
from app.main import app

TENANT_ID = uuid.uuid4()
ACCOUNT_ID = "acct_123"


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture
def client(session):
    async def override_session():
        yield session

    app.dependency_overrides[get_system_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_assinatura_invalida_retorna_400(client, session, monkeypatch):
    import stripe

    def _raise(*args, **kwargs):
        raise stripe.error.SignatureVerificationError("inválida", "sig")

    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", _raise)

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-invalida"},
    )

    assert response.status_code == 400


def test_checkout_completed_resolve_tenant_pelo_stripe_account_id(client, session, monkeypatch):
    session.scalar = AsyncMock(
        return_value=SimpleNamespace(tenant_id=TENANT_ID, stripe_account_id=ACCOUNT_ID)
    )
    event = {
        "type": "checkout.session.completed",
        "account": ACCOUNT_ID,
        "data": {"object": {"id": "cs_1"}},
    }
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process = AsyncMock()
    monkeypatch.setattr(webhook_module, "process_end_customer_checkout_completed", process)

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process.assert_awaited_once()
    assert process.await_args.args[1] == TENANT_ID


def test_evento_sem_tenant_resolvido_e_ignorado(client, session, monkeypatch):
    session.scalar = AsyncMock(return_value=None)
    event = {
        "type": "checkout.session.completed",
        "account": "acct_desconhecido",
        "data": {"object": {"id": "cs_1"}},
    }
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process = AsyncMock()
    monkeypatch.setattr(webhook_module, "process_end_customer_checkout_completed", process)

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process.assert_not_awaited()


def test_evento_de_status_atualiza_stripe_account_status(client, session, monkeypatch):
    row = SimpleNamespace(
        tenant_id=TENANT_ID, stripe_account_id=ACCOUNT_ID, stripe_account_status=None
    )
    session.scalar = AsyncMock(return_value=row)
    event = {
        "type": webhook_module._STATUS_EVENT_TYPE,
        "account": ACCOUNT_ID,
        "data": {"object": {"id": ACCOUNT_ID}},
    }
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    monkeypatch.setattr(webhook_module, "_resolve_account_status", AsyncMock(return_value="active"))

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    assert row.stripe_account_status == "active"
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_resolve_account_status_le_shape_v1_flat_de_capabilities():
    """`account.updated` (evento v1/snapshot) entrega `capabilities` como um
    dict de strings (`{"card_payments": "active"}`), não o shape aninhado
    `configuration.merchant.capabilities.card_payments.status` da API v2 —
    ver docstring de stripe_connect.py."""
    assert await _resolve_account_status({"capabilities": {"card_payments": "active"}}) == "active"
    assert (
        await _resolve_account_status({"capabilities": {"card_payments": "pending"}})
        == "onboarding"
    )
    assert await _resolve_account_status({"capabilities": {}}) == "onboarding"
    assert await _resolve_account_status({}) == "onboarding"
