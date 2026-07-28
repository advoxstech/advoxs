import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app
from app.schemas.end_customer_billing import RevenueByMonthOut, RevenueReportOut

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


def test_tenant_standalone_retorna_404(client, session):
    session.scalar.return_value = SimpleNamespace(billing_provider="standalone")

    response = client.get("/api/v1/end-customer-billing/revenue?from=2026-07-01&to=2026-07-31")

    assert response.status_code == 404


def test_tenant_connect_retorna_relatorio(client, session, monkeypatch):
    import app.api.v1.end_customer_billing as routes_module

    session.scalar.return_value = SimpleNamespace(billing_provider="connect")
    report = RevenueReportOut(
        by_month=[RevenueByMonthOut(month="2026-07", total_brl=179.70)], by_customer=[]
    )
    get_revenue_report_mock = AsyncMock(return_value=report)
    monkeypatch.setattr(routes_module, "get_revenue_report", get_revenue_report_mock)

    response = client.get("/api/v1/end-customer-billing/revenue?from=2026-07-01&to=2026-07-31")

    assert response.status_code == 200
    assert response.json()["by_month"][0]["total_brl"] == 179.70
    get_revenue_report_mock.assert_awaited_once_with(
        session, TENANT_ID, date(2026, 7, 1), date(2026, 7, 31)
    )


def test_to_anterior_a_from_retorna_422(client, session):
    session.scalar.return_value = SimpleNamespace(billing_provider="connect")

    response = client.get("/api/v1/end-customer-billing/revenue?from=2026-07-31&to=2026-07-01")

    assert response.status_code == 422


def test_sem_token_retorna_401():
    response = TestClient(app).get(
        "/api/v1/end-customer-billing/revenue?from=2026-07-01&to=2026-07-31"
    )
    assert response.status_code == 401
