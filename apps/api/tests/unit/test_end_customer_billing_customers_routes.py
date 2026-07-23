import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.end_customer_billing as end_customer_billing_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app
from app.schemas.end_customer_billing import EndCustomerSummaryOut

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


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/end-customer-billing/customers")
    assert response.status_code == 401


def test_lista_clientes_delegando_ao_service(client, session, monkeypatch) -> None:
    fake = AsyncMock(
        return_value=[
            EndCustomerSummaryOut(
                contact_phone_number="5511999990001",
                credit_balance=120.0,
                total_purchased=500.0,
                total_consumed=380.0,
            )
        ]
    )
    monkeypatch.setattr(end_customer_billing_module, "list_customers", fake)

    response = client.get("/api/v1/end-customer-billing/customers")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["contact_phone_number"] == "5511999990001"
    assert body[0]["total_consumed"] == 380.0
    fake.assert_awaited_once_with(session, TENANT_ID, 50, 0)


def test_respeita_limit_e_offset(client, session, monkeypatch) -> None:
    fake = AsyncMock(return_value=[])
    monkeypatch.setattr(end_customer_billing_module, "list_customers", fake)

    response = client.get("/api/v1/end-customer-billing/customers?limit=10&offset=5")

    assert response.status_code == 200
    fake.assert_awaited_once_with(session, TENANT_ID, 10, 5)


def test_zerar_saldo_delegando_ao_service(client, session, monkeypatch) -> None:
    fake = AsyncMock(return_value=None)
    monkeypatch.setattr(end_customer_billing_module, "zero_end_customer_balance", fake)

    response = client.post("/api/v1/end-customer-billing/customers/5511999990001/zero-balance")

    assert response.status_code == 204
    fake.assert_awaited_once_with(session, TENANT_ID, "5511999990001")


def test_zerar_saldo_contato_sem_saldo_retorna_404(client, session, monkeypatch) -> None:
    fake = AsyncMock(
        side_effect=end_customer_billing_module.EndCustomerBalanceNotFoundError("sem saldo")
    )
    monkeypatch.setattr(end_customer_billing_module, "zero_end_customer_balance", fake)

    response = client.post("/api/v1/end-customer-billing/customers/5511999990001/zero-balance")

    assert response.status_code == 404
