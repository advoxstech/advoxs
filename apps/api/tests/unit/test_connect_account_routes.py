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
