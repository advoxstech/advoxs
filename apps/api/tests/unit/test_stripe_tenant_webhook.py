import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.webhooks.stripe_tenant as webhook_module
from app.core.db import get_system_session
from app.core.queue import get_arq_pool
from app.main import app

TENANT_ID = uuid.uuid4()


def _settings_row(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        tenant_id=TENANT_ID, enabled=True, stripe_webhook_secret_encrypted="cifrado"
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture
def client(session):
    async def override_session():
        yield session

    async def override_arq():
        return AsyncMock()

    app.dependency_overrides[get_system_session] = override_session
    app.dependency_overrides[get_arq_pool] = override_arq
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_tenant_sem_webhook_secret_configurado_retorna_400(client, session) -> None:
    session.scalar = AsyncMock(return_value=None)

    response = client.post(
        f"/api/v1/webhooks/stripe/tenant/{TENANT_ID}",
        content=b"{}",
        headers={"Stripe-Signature": "sig"},
    )

    assert response.status_code == 400


def test_assinatura_invalida_retorna_400(client, session, monkeypatch) -> None:
    session.scalar = AsyncMock(return_value=_settings_row())
    monkeypatch.setattr(webhook_module, "decrypt_tenant_secret", lambda v: "whsec_do_tenant")

    def _raise(*args, **kwargs):
        raise webhook_module.stripe.error.SignatureVerificationError("inválida", "sig")

    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", _raise)

    response = client.post(
        f"/api/v1/webhooks/stripe/tenant/{TENANT_ID}",
        content=b"{}",
        headers={"Stripe-Signature": "sig-invalida"},
    )

    assert response.status_code == 400


def test_checkout_completed_processa_evento(client, session, monkeypatch) -> None:
    session.scalar = AsyncMock(return_value=_settings_row())
    monkeypatch.setattr(webhook_module, "decrypt_tenant_secret", lambda v: "whsec_do_tenant")
    event = {"type": "checkout.session.completed", "data": {"object": {"id": "cs_1"}}}
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process = AsyncMock()
    monkeypatch.setattr(webhook_module, "process_end_customer_checkout_completed", process)

    response = client.post(
        f"/api/v1/webhooks/stripe/tenant/{TENANT_ID}",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process.assert_awaited_once()
    assert process.await_args.args[1] == TENANT_ID


def test_evento_diferente_e_ignorado(client, session, monkeypatch) -> None:
    session.scalar = AsyncMock(return_value=_settings_row())
    monkeypatch.setattr(webhook_module, "decrypt_tenant_secret", lambda v: "whsec_do_tenant")
    event = {"type": "payment_intent.succeeded", "data": {"object": {}}}
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process = AsyncMock()
    monkeypatch.setattr(webhook_module, "process_end_customer_checkout_completed", process)

    response = client.post(
        f"/api/v1/webhooks/stripe/tenant/{TENANT_ID}",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process.assert_not_awaited()
