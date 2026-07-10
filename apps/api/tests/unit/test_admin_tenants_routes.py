import uuid
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import app.api.v1.platform_admin.tenants as tenants_module
from app.api.deps import PlatformAdminContext, get_current_platform_admin
from app.core.db import get_system_session
from app.main import app

TENANT_ID = uuid.uuid4()


def _client(monkeypatch):
    async def override_admin():
        return PlatformAdminContext(admin_id=uuid.uuid4(), role="superadmin")

    async def override_session():
        yield AsyncMock()

    app.dependency_overrides[get_current_platform_admin] = override_admin
    app.dependency_overrides[get_system_session] = override_session
    return TestClient(app)


def test_lista_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/platform-admin/tenants")
    assert response.status_code == 401


def test_detalhe_tenant_nao_encontrado_retorna_404(monkeypatch) -> None:
    monkeypatch.setattr(tenants_module, "get_tenant_detail", AsyncMock(return_value=None))
    client = _client(monkeypatch)
    try:
        response = client.get(f"/api/v1/platform-admin/tenants/{TENANT_ID}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
