import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.services.zapi_connection as zapi_connection_module
from app.api.deps import PlatformAdminContext, get_current_platform_admin
from app.clients.zapi import ZApiApiError, ZApiNetworkError
from app.core.db import get_system_session
from app.main import app

TENANT_ID = uuid.uuid4()

CONNECT_ZAPI_BODY = {
    "instance_id": "inst-123",
    "instance_token": "token-claro",
    "client_token": "client-token-claro",
}


@pytest.fixture
def session():
    mock = AsyncMock()
    mock.add = MagicMock()
    mock.get = AsyncMock(return_value=SimpleNamespace(id=TENANT_ID))
    return mock


@pytest.fixture
def client(session):
    async def override_admin():
        return PlatformAdminContext(admin_id=uuid.uuid4(), role="superadmin")

    async def override_session():
        yield session

    app.dependency_overrides[get_current_platform_admin] = override_admin
    app.dependency_overrides[get_system_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def zapi_mocks(monkeypatch):
    mocks = {
        "check_status": AsyncMock(return_value={"connected": False}),
        "configure_webhook": AsyncMock(return_value=None),
        "encrypt": MagicMock(side_effect=lambda v: f"cifrado:{v}"),
    }
    monkeypatch.setattr(zapi_connection_module, "check_zapi_status", mocks["check_status"])
    monkeypatch.setattr(
        zapi_connection_module, "configure_zapi_webhook", mocks["configure_webhook"]
    )
    monkeypatch.setattr(zapi_connection_module, "encrypt_access_token", mocks["encrypt"])
    monkeypatch.setattr(
        zapi_connection_module.settings, "api_public_url", "https://api.exemplo.com.br"
    )
    return mocks


class TestProvisionTenantZApi:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).post(
            f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp/zapi",
            json=CONNECT_ZAPI_BODY,
        )
        assert response.status_code == 401

    def test_provisiona_com_sucesso_e_marca_managed_by_advoxs(
        self, client, session, zapi_mocks
    ) -> None:
        session.scalar.return_value = None

        response = client.post(
            f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp/zapi",
            json=CONNECT_ZAPI_BODY,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "zapi"
        assert body["status"] == "disconnected"
        assert body["managed_by_advoxs"] is True
        session.add.assert_called()  # WhatsAppNumber novo + AdminAuditLog

    def test_sem_client_token_retorna_422(self, client, session, zapi_mocks) -> None:
        body = {**CONNECT_ZAPI_BODY, "client_token": ""}

        response = client.post(
            f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp/zapi",
            json=body,
        )

        assert response.status_code == 422
        zapi_mocks["check_status"].assert_not_awaited()

    def test_tenant_inexistente_retorna_404_sem_chamar_z_api(
        self, client, session, zapi_mocks
    ) -> None:
        session.get = AsyncMock(return_value=None)

        response = client.post(
            f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp/zapi",
            json=CONNECT_ZAPI_BODY,
        )

        assert response.status_code == 404
        zapi_mocks["check_status"].assert_not_awaited()

    def test_credenciais_invalidas_retorna_400_sem_persistir(
        self, client, session, zapi_mocks
    ) -> None:
        zapi_mocks["check_status"].side_effect = ZApiApiError("credenciais inválidas")

        response = client.post(
            f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp/zapi",
            json=CONNECT_ZAPI_BODY,
        )

        assert response.status_code == 400
        session.commit.assert_not_awaited()

    def test_falha_de_rede_retorna_502(self, client, session, zapi_mocks) -> None:
        zapi_mocks["check_status"].side_effect = ZApiNetworkError("timeout")

        response = client.post(
            f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp/zapi",
            json=CONNECT_ZAPI_BODY,
        )

        assert response.status_code == 502

    def test_provisionar_resolve_pedido_pendente_do_tenant(
        self, client, session, zapi_mocks
    ) -> None:
        """Se o tenant tinha pedido a conexão gerenciada, provisionar a
        instância fecha esse pedido na mesma transação — sem passo manual
        extra de "marcar como atendido"."""
        pending_request = SimpleNamespace(
            tenant_id=TENANT_ID,
            status="pending",
            requested_at=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
            resolved_at=None,
        )
        # 1ª chamada a session.scalar: lookup do WhatsAppNumber existente
        # (dentro de provision_zapi_connection). 2ª: lookup do pedido
        # pendente (dentro de resolve_pending_request).
        session.scalar = AsyncMock(side_effect=[None, pending_request])

        response = client.post(
            f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp/zapi",
            json=CONNECT_ZAPI_BODY,
        )

        assert response.status_code == 200
        assert pending_request.status == "fulfilled"
        assert pending_request.resolved_at is not None

    def test_provisionar_sem_pedido_pendente_nao_quebra(self, client, session, zapi_mocks) -> None:
        session.scalar = AsyncMock(side_effect=[None, None])

        response = client.post(
            f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp/zapi",
            json=CONNECT_ZAPI_BODY,
        )

        assert response.status_code == 200


class TestGetTenantWhatsApp:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).get(f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp")
        assert response.status_code == 401

    def test_sem_conexao_retorna_null(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.get(f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp")

        assert response.status_code == 200
        assert response.json() is None


class TestGetTenantWhatsAppRequest:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).get(
            f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp-request"
        )
        assert response.status_code == 401

    def test_sem_pedido_retorna_null(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.get(f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp-request")

        assert response.status_code == 200
        assert response.json() is None

    def test_com_pedido_pendente_retorna_status_e_data(self, client, session) -> None:
        session.scalar.return_value = SimpleNamespace(
            tenant_id=TENANT_ID,
            status="pending",
            requested_at=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        )

        response = client.get(f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp-request")

        assert response.status_code == 200
        assert response.json()["status"] == "pending"
