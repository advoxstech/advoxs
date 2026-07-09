import uuid
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import app.api.v1.platform_admin.playground as playground_module
from app.api.deps import PlatformAdminContext, get_current_platform_admin
from app.clients.agents import AgentsApiError, AgentsNetworkError
from app.core.db import get_session
from app.main import app
from app.schemas.playground import PlaygroundMessageOut
from app.services.playground import TenantNotFoundError

TENANT_ID = uuid.uuid4()
BODY = {"tenant_id": str(TENANT_ID), "session_id": "sess-1", "message": "olá"}


def _client():
    async def override_admin():
        return PlatformAdminContext(admin_id=uuid.uuid4(), role="superadmin")

    async def override_session():
        yield AsyncMock()

    app.dependency_overrides[get_current_platform_admin] = override_admin
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


class TestSendMessageRoute:
    def test_sem_token_retorna_401(self):
        response = TestClient(app).post("/api/v1/platform-admin/playground/messages", json=BODY)
        assert response.status_code == 401

    def test_sucesso_retorna_200(self, monkeypatch):
        monkeypatch.setattr(
            playground_module,
            "send_message",
            AsyncMock(
                return_value=PlaygroundMessageOut(
                    responses=["oi!"],
                    tokens_used=100,
                    current_agent="agente_secretaria",
                    grouped=False,
                )
            ),
        )
        client = _client()
        try:
            response = client.post("/api/v1/platform-admin/playground/messages", json=BODY)
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json()["responses"] == ["oi!"]

    def test_tenant_inexistente_retorna_404(self, monkeypatch):
        monkeypatch.setattr(
            playground_module, "send_message", AsyncMock(side_effect=TenantNotFoundError())
        )
        client = _client()
        try:
            response = client.post("/api/v1/platform-admin/playground/messages", json=BODY)
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    def test_erro_do_agents_retorna_502(self, monkeypatch):
        monkeypatch.setattr(
            playground_module, "send_message", AsyncMock(side_effect=AgentsApiError("HTTP 500"))
        )
        client = _client()
        try:
            response = client.post("/api/v1/platform-admin/playground/messages", json=BODY)
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 502

    def test_erro_de_rede_retorna_502(self, monkeypatch):
        monkeypatch.setattr(
            playground_module, "send_message", AsyncMock(side_effect=AgentsNetworkError("timeout"))
        )
        client = _client()
        try:
            response = client.post("/api/v1/platform-admin/playground/messages", json=BODY)
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 502

    def test_mensagem_vazia_retorna_422(self):
        client = _client()
        try:
            response = client.post(
                "/api/v1/platform-admin/playground/messages",
                json={**BODY, "message": ""},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422


class TestDeleteConversationRoute:
    def test_sem_token_retorna_401(self):
        response = TestClient(app).delete(
            f"/api/v1/platform-admin/playground/conversations/{TENANT_ID}/sess-1"
        )
        assert response.status_code == 401

    def test_sucesso_retorna_204(self, monkeypatch):
        monkeypatch.setattr(playground_module, "delete_conversation", AsyncMock())
        client = _client()
        try:
            response = client.delete(
                f"/api/v1/platform-admin/playground/conversations/{TENANT_ID}/sess-1"
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 204
