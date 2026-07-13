import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.internal.end_customer_billing as internal_module
from app.core.config import settings
from app.core.db import get_system_session
from app.main import app
from app.services.end_customer_billing import (
    BillingNotConfiguredError,
    InvalidPackageError,
    StripeApiError,
)

TENANT_ID = str(uuid.uuid4())
PACKAGE_ID = str(uuid.uuid4())
PAYLOAD = {
    "tenant_id": TENANT_ID,
    "contact_phone_number": "5511999998888",
    "package_id": PACKAGE_ID,
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_key", "")

    async def override_session():
        yield AsyncMock()

    app.dependency_overrides[get_system_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_sem_api_key_com_env_configurada_retorna_403(monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_service_key", "chave-interna")

    response = TestClient(app).post("/api/v1/internal/end-customer-billing/checkout", json=PAYLOAD)

    assert response.status_code == 403


def test_sucesso_retorna_checkout_url(client, monkeypatch) -> None:
    monkeypatch.setattr(
        internal_module,
        "create_end_customer_checkout_session",
        AsyncMock(return_value="https://checkout.stripe.com/pay/cs_1"),
    )

    response = client.post("/api/v1/internal/end-customer-billing/checkout", json=PAYLOAD)

    assert response.status_code == 200
    assert response.json() == {"checkout_url": "https://checkout.stripe.com/pay/cs_1"}


def test_billing_nao_configurado_retorna_404(client, monkeypatch) -> None:
    monkeypatch.setattr(
        internal_module,
        "create_end_customer_checkout_session",
        AsyncMock(side_effect=BillingNotConfiguredError("não configurado")),
    )

    response = client.post("/api/v1/internal/end-customer-billing/checkout", json=PAYLOAD)

    assert response.status_code == 404


def test_pacote_invalido_retorna_400(client, monkeypatch) -> None:
    monkeypatch.setattr(
        internal_module,
        "create_end_customer_checkout_session",
        AsyncMock(side_effect=InvalidPackageError("inválido")),
    )

    response = client.post("/api/v1/internal/end-customer-billing/checkout", json=PAYLOAD)

    assert response.status_code == 400


def test_falha_na_stripe_retorna_502(client, monkeypatch) -> None:
    monkeypatch.setattr(
        internal_module,
        "create_end_customer_checkout_session",
        AsyncMock(side_effect=StripeApiError("falhou")),
    )

    response = client.post("/api/v1/internal/end-customer-billing/checkout", json=PAYLOAD)

    assert response.status_code == 502
