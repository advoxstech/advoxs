import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app

TENANT_ID = uuid.uuid4()


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture
def client(session):
    async def override_ctx():
        return TenantContext(user_id=uuid.uuid4(), tenant_id=TENANT_ID, role="admin")

    async def override_session():
        yield session

    app.dependency_overrides[get_current_tenant] = override_ctx
    app.dependency_overrides[get_tenant_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_post_connect_account_devolve_client_secret(client, session, monkeypatch):
    import app.api.v1.end_customer_billing as routes_module

    monkeypatch.setattr(
        routes_module, "create_or_refresh_connect_account", AsyncMock(return_value="secret_abc")
    )

    response = client.post("/api/v1/end-customer-billing/connect-account")

    assert response.status_code == 200
    assert response.json()["client_secret"] == "secret_abc"


def test_post_connect_account_erro_da_stripe_retorna_502(client, session, monkeypatch):
    import app.api.v1.end_customer_billing as routes_module
    from app.services.stripe_connect import ConnectApiError

    async def _raise(*args, **kwargs):
        raise ConnectApiError("falhou")

    monkeypatch.setattr(routes_module, "create_or_refresh_connect_account", _raise)

    response = client.post("/api/v1/end-customer-billing/connect-account")

    assert response.status_code == 502


def test_sem_token_retorna_401_no_connect_account():
    response = TestClient(app).post("/api/v1/end-customer-billing/connect-account")
    assert response.status_code == 401


def _settings_row(**overrides):
    from types import SimpleNamespace

    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        billing_provider="connect",
        stripe_account_id="acct_123",
        stripe_account_status="active",
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def test_get_earnings_devolve_saldo_e_repasses(client, session, monkeypatch):
    import app.api.v1.end_customer_billing as routes_module
    from app.schemas.end_customer_billing import ConnectEarningsOut, ConnectPayoutOut

    session.scalar.return_value = _settings_row()
    fake = AsyncMock(
        return_value=ConnectEarningsOut(
            available_brl=123.45,
            pending_brl=20.0,
            recent_payouts=[
                ConnectPayoutOut(amount_brl=100.0, status="paid", arrival_date="2026-07-20")
            ],
        )
    )
    monkeypatch.setattr(routes_module, "get_account_earnings", fake)

    response = client.get("/api/v1/end-customer-billing/connect-account/earnings")

    assert response.status_code == 200
    body = response.json()
    assert body["available_brl"] == 123.45
    assert body["recent_payouts"][0]["status"] == "paid"
    fake.assert_awaited_once_with("acct_123")


def test_get_earnings_sem_conta_connect_retorna_404(client, session, monkeypatch):
    import app.api.v1.end_customer_billing as routes_module

    session.scalar.return_value = _settings_row(stripe_account_id=None)
    fake = AsyncMock()
    monkeypatch.setattr(routes_module, "get_account_earnings", fake)

    response = client.get("/api/v1/end-customer-billing/connect-account/earnings")

    assert response.status_code == 404
    fake.assert_not_awaited()


def test_get_earnings_tenant_standalone_retorna_404(client, session, monkeypatch):
    import app.api.v1.end_customer_billing as routes_module

    session.scalar.return_value = _settings_row(billing_provider="standalone")
    fake = AsyncMock()
    monkeypatch.setattr(routes_module, "get_account_earnings", fake)

    response = client.get("/api/v1/end-customer-billing/connect-account/earnings")

    assert response.status_code == 404
    fake.assert_not_awaited()


def test_get_earnings_erro_da_stripe_retorna_502(client, session, monkeypatch):
    import app.api.v1.end_customer_billing as routes_module
    from app.services.stripe_connect import ConnectApiError

    session.scalar.return_value = _settings_row()

    async def _raise(*args, **kwargs):
        raise ConnectApiError("falhou")

    monkeypatch.setattr(routes_module, "get_account_earnings", _raise)

    response = client.get("/api/v1/end-customer-billing/connect-account/earnings")

    assert response.status_code == 502


def test_sem_token_retorna_401_nos_earnings():
    response = TestClient(app).get("/api/v1/end-customer-billing/connect-account/earnings")
    assert response.status_code == 401
