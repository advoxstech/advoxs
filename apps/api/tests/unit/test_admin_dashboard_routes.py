import uuid
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import app.api.v1.platform_admin.dashboard as dashboard_module
from app.api.deps import PlatformAdminContext, get_current_platform_admin
from app.core.db import get_system_session
from app.main import app
from app.schemas.admin_dashboard import (
    AdminDashboardOut,
    CreditsSummary,
    KnowledgeBaseUsageSummary,
    TenantsByStatus,
    WhatsappConnectedSummary,
)


def _dummy_dashboard() -> AdminDashboardOut:
    return AdminDashboardOut(
        tenants_total=1,
        tenants_by_status=TenantsByStatus(active=1, suspended=0),
        new_tenants_last_30_days=[],
        revenue_brl_last_30_days=0,
        credits_summary=CreditsSummary(sold=0, consumed=0),
        messages_processed=0,
        agent_executions=0,
        tokens_consumed=0,
        low_balance_tenants=[],
        whatsapp_connected=WhatsappConnectedSummary(connected=0, total=1),
        knowledge_base_usage=KnowledgeBaseUsageSummary(total_files=0, total_size_bytes=0),
    )


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/platform-admin/dashboard")
    assert response.status_code == 401


def test_com_token_retorna_o_dashboard(monkeypatch) -> None:
    async def override_admin():
        return PlatformAdminContext(admin_id=uuid.uuid4(), role="superadmin")

    async def override_session():
        yield AsyncMock()

    monkeypatch.setattr(
        dashboard_module, "build_dashboard", AsyncMock(return_value=_dummy_dashboard())
    )
    app.dependency_overrides[get_current_platform_admin] = override_admin
    app.dependency_overrides[get_system_session] = override_session
    try:
        response = TestClient(app).get("/api/v1/platform-admin/dashboard")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["tenants_total"] == 1
