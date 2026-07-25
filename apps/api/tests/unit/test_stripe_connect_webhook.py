import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import stripe
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


def test_checkout_completed_com_evento_stripeobject_real(client, session, monkeypatch):
    """Regressão: `event` (retorno de `stripe.Webhook.construct_event`) é um
    `stripe.Event` real — subclasse de `StripeObject`, sem `.get()` (só
    `[]`/`in`; `.get` em si já levanta `AttributeError('get')` antes da
    chamada acontecer). Os outros testes deste arquivo mockam `construct_event`
    devolvendo um `dict` puro, que mascara esse bug porque dict tem `.get()`.
    Sem o fix (`event.get("account")` em vez de `.to_dict().get("account")`),
    todo webhook Connect real quebra com 500 logo após a verificação de
    assinatura — nunca chega a resolver o tenant."""
    session.scalar = AsyncMock(
        return_value=SimpleNamespace(tenant_id=TENANT_ID, stripe_account_id=ACCOUNT_ID)
    )
    real_event = stripe.Event.construct_from(
        {
            "id": "evt_1",
            "type": "checkout.session.completed",
            "account": ACCOUNT_ID,
            "data": {"object": {"id": "cs_1"}},
        },
        "sk_test_fake",
    )
    monkeypatch.setattr(
        webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: real_event
    )
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


@pytest.mark.asyncio
async def test_resolve_account_status_com_account_e_capabilities_stripeobject_real():
    """Regressão: `event["data"]["object"]` de um `account.updated` real é um
    `stripe.Account` real, e `capabilities` dentro dele é um
    `Account.Capabilities` — StripeObject aninhado, também sem `.get()`.
    Sem o fix, `_resolve_account_status` quebra com `AttributeError('get')`
    tanto no primeiro `.get("capabilities", {})` quanto no
    `capabilities.get("card_payments")` seguinte — o teste
    `test_resolve_account_status_le_shape_v1_flat_de_capabilities` abaixo só
    passa dict puro e não pega nenhum dos dois."""
    real_account_active = stripe.Account.construct_from(
        {"id": ACCOUNT_ID, "capabilities": {"card_payments": "active"}},
        "sk_test_fake",
    )
    assert await _resolve_account_status(real_account_active) == "active"

    real_account_pending = stripe.Account.construct_from(
        {"id": ACCOUNT_ID, "capabilities": {"card_payments": "pending"}},
        "sk_test_fake",
    )
    assert await _resolve_account_status(real_account_pending) == "onboarding"

    real_account_sem_capabilities = stripe.Account.construct_from(
        {"id": ACCOUNT_ID},
        "sk_test_fake",
    )
    assert await _resolve_account_status(real_account_sem_capabilities) == "onboarding"


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


def test_checkout_completed_de_assinatura_chama_process_subscription_created(
    client, session, monkeypatch
):
    session.scalar = AsyncMock(
        return_value=SimpleNamespace(tenant_id=TENANT_ID, stripe_account_id=ACCOUNT_ID)
    )
    event = {
        "type": "checkout.session.completed",
        "account": ACCOUNT_ID,
        "data": {"object": {"id": "cs_1", "subscription": "sub_1"}},
    }
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process_purchase = AsyncMock()
    process_subscription = AsyncMock()
    monkeypatch.setattr(webhook_module, "process_end_customer_checkout_completed", process_purchase)
    monkeypatch.setattr(
        webhook_module, "process_end_customer_subscription_created", process_subscription
    )

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process_purchase.assert_awaited_once()
    process_subscription.assert_awaited_once()


def test_invoice_payment_succeeded_chama_process_renewed(client, session, monkeypatch):
    session.scalar = AsyncMock(
        return_value=SimpleNamespace(tenant_id=TENANT_ID, stripe_account_id=ACCOUNT_ID)
    )
    event = {
        "type": "invoice.payment_succeeded",
        "account": ACCOUNT_ID,
        "data": {"object": {"subscription": "sub_1"}},
    }
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process_renewed = AsyncMock()
    monkeypatch.setattr(
        webhook_module, "process_end_customer_subscription_renewed", process_renewed
    )

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process_renewed.assert_awaited_once()
    assert process_renewed.await_args.args[1] == TENANT_ID


def test_customer_subscription_deleted_notifica_cancelamento(client, session, monkeypatch):
    session.scalar = AsyncMock(
        return_value=SimpleNamespace(tenant_id=TENANT_ID, stripe_account_id=ACCOUNT_ID)
    )
    event = {
        "type": "customer.subscription.deleted",
        "account": ACCOUNT_ID,
        "data": {"object": {"id": "sub_1", "status": "canceled"}},
    }
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process_status = AsyncMock()
    monkeypatch.setattr(
        webhook_module, "process_end_customer_subscription_status_changed", process_status
    )

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process_status.assert_awaited_once()
    assert process_status.await_args.kwargs["notify_cancel"] is True


def test_customer_subscription_updated_nao_notifica_cancelamento(client, session, monkeypatch):
    session.scalar = AsyncMock(
        return_value=SimpleNamespace(tenant_id=TENANT_ID, stripe_account_id=ACCOUNT_ID)
    )
    event = {
        "type": "customer.subscription.updated",
        "account": ACCOUNT_ID,
        "data": {"object": {"id": "sub_1", "status": "past_due"}},
    }
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process_status = AsyncMock()
    monkeypatch.setattr(
        webhook_module, "process_end_customer_subscription_status_changed", process_status
    )

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process_status.assert_awaited_once()
    assert process_status.await_args.kwargs["notify_cancel"] is False
