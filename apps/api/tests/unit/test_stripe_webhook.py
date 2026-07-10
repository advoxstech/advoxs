from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.webhooks.stripe as stripe_webhook_module
from app.core.db import get_system_session
from app.main import app


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


def test_assinatura_invalida_retorna_400(client, monkeypatch) -> None:
    def _raise(*args, **kwargs):
        raise stripe_webhook_module.stripe.error.SignatureVerificationError("inválida", "sig")

    monkeypatch.setattr(stripe_webhook_module.stripe.Webhook, "construct_event", _raise)

    response = client.post(
        "/api/v1/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "sig-invalida"}
    )

    assert response.status_code == 400


def test_checkout_completed_processa_evento(client, monkeypatch) -> None:
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_123", "metadata": {}}},
    }
    monkeypatch.setattr(
        stripe_webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event
    )
    process = AsyncMock()
    monkeypatch.setattr(stripe_webhook_module, "process_checkout_completed", process)

    response = client.post(
        "/api/v1/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "sig-valida"}
    )

    assert response.status_code == 200
    process.assert_awaited_once()


def test_evento_diferente_e_ignorado(client, monkeypatch) -> None:
    event = {"type": "payment_intent.succeeded", "data": {"object": {}}}
    monkeypatch.setattr(
        stripe_webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event
    )
    process = AsyncMock()
    monkeypatch.setattr(stripe_webhook_module, "process_checkout_completed", process)

    response = client.post(
        "/api/v1/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "sig-valida"}
    )

    assert response.status_code == 200
    process.assert_not_awaited()
