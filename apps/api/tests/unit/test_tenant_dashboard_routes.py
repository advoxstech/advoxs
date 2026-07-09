import uuid
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import app.api.v1.dashboard as dashboard_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app
from app.schemas.dashboard import (
    ConversationsSummaryOut,
    KnowledgeBaseSummaryOut,
    TenantDashboardOut,
    UsageSummaryOut,
    WhatsappStatusOut,
)

TENANT_ID = uuid.uuid4()


def _dummy_dashboard() -> TenantDashboardOut:
    return TenantDashboardOut(
        credit_balance=1000,
        whatsapp=WhatsappStatusOut(connected=True, display_phone_number="551 **** 4321"),
        conversations=ConversationsSummaryOut(total=2, waiting_human=1),
        usage_last_30_days=UsageSummaryOut(agent_messages=10, credits_consumed=5),
        knowledge_base=KnowledgeBaseSummaryOut(ready=3, error=0),
        recent_conversations=[],
    )


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/dashboard")
    assert response.status_code == 401


def test_com_token_retorna_o_dashboard(monkeypatch) -> None:
    async def override_tenant():
        return TenantContext(user_id=uuid.uuid4(), tenant_id=TENANT_ID, role="admin")

    async def override_session():
        yield AsyncMock()

    build = AsyncMock(return_value=_dummy_dashboard())
    monkeypatch.setattr(dashboard_module, "build_tenant_dashboard", build)
    app.dependency_overrides[get_current_tenant] = override_tenant
    app.dependency_overrides[get_tenant_session] = override_session
    try:
        response = TestClient(app).get("/api/v1/dashboard")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["credit_balance"] == 1000
    assert body["whatsapp"]["connected"] is True
    # O tenant_id passado ao service vem do contexto autenticado.
    assert build.await_args.args[1] == TENANT_ID
