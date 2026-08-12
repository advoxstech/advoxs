import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

import app.api.v1.whatsapp as whatsapp_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.whatsapp import WhatsAppApiError, WhatsAppNetworkError
from app.main import app

TENANT_ID = uuid.uuid4()

CONNECT_BODY = {
    "phone_number_id": "PNID",
    "waba_id": "WABA",
    "access_token": "token-claro",
    "pin": "123456",
}


def _number(status: str = "connected", provider: str = "meta") -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id=TENANT_ID,
        provider=provider,
        phone_number_id="PNID-antigo",
        waba_id="WABA-antigo",
        zapi_instance_id=None,
        zapi_instance_token_encrypted=None,
        zapi_client_token_encrypted=None,
        zapi_managed_by_advoxs=False,
        display_phone_number="+5511987654321",
        access_token_encrypted="cifrado",
        status=status,
        connected_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    )


@pytest.fixture
def session():
    mock = AsyncMock()
    mock.add = MagicMock()

    async def fake_refresh(obj):
        if getattr(obj, "connected_at", None) is None:
            obj.connected_at = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)

    mock.refresh.side_effect = fake_refresh
    return mock


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


@pytest.fixture
def graph_mocks(monkeypatch):
    mocks = {
        "fetch": AsyncMock(return_value="+5511987654321"),
        "register": AsyncMock(return_value=None),
        "subscribe": AsyncMock(return_value=None),
        "encrypt": MagicMock(return_value="token-cifrado"),
    }
    monkeypatch.setattr(whatsapp_module, "fetch_display_phone_number", mocks["fetch"])
    monkeypatch.setattr(whatsapp_module, "register_number", mocks["register"])
    monkeypatch.setattr(whatsapp_module, "subscribe_app_to_waba", mocks["subscribe"])
    monkeypatch.setattr(whatsapp_module, "encrypt_access_token", mocks["encrypt"])
    return mocks


def test_connect_sem_token_retorna_401() -> None:
    response = TestClient(app).post("/api/v1/whatsapp/connect", json=CONNECT_BODY)
    assert response.status_code == 401


class TestConnect:
    def test_conexao_feliz_nova(self, client, session, graph_mocks) -> None:
        session.scalar.return_value = None

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "connected"
        assert body["display_phone_number"] == "+55 **** 4321"
        session.add.assert_called_once()
        graph_mocks["fetch"].assert_awaited_once_with("PNID", "token-claro")
        graph_mocks["register"].assert_awaited_once_with("PNID", "token-claro", "123456")
        graph_mocks["subscribe"].assert_awaited_once_with("WABA", "token-claro")

    def test_reconexao_substitui_linha_existente(self, client, session, graph_mocks) -> None:
        existing = _number(status="disconnected")
        session.scalar.return_value = existing

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 200
        assert existing.status == "connected"
        assert existing.phone_number_id == "PNID"
        assert existing.waba_id == "WABA"
        session.add.assert_not_called()

    def test_reconexao_via_meta_reseta_provider_e_limpa_campos_zapi(
        self, client, session, graph_mocks
    ) -> None:
        existing = _number(status="disconnected", provider="zapi")
        existing.zapi_instance_id = "inst-x"
        existing.zapi_instance_token_encrypted = "cifrado-token"
        existing.zapi_client_token_encrypted = "cifrado-client-token"
        existing.zapi_webhook_secret = "segredo-abc"
        session.scalar.return_value = existing

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 200
        assert existing.provider == "meta"
        assert existing.zapi_instance_id is None
        assert existing.zapi_instance_token_encrypted is None
        assert existing.zapi_client_token_encrypted is None
        assert existing.zapi_webhook_secret is None

    def test_falha_no_get_retorna_400_sem_persistir(self, client, session, graph_mocks) -> None:
        graph_mocks["fetch"].side_effect = WhatsAppApiError("token inválido")

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 400
        assert response.json()["detail"] == "token inválido"
        graph_mocks["register"].assert_not_awaited()
        session.commit.assert_not_awaited()

    def test_falha_no_register_retorna_400_sem_persistir(
        self, client, session, graph_mocks
    ) -> None:
        graph_mocks["register"].side_effect = WhatsAppApiError("PIN incorreto")

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 400
        session.commit.assert_not_awaited()

    def test_falha_de_rede_no_get_retorna_502(self, client, session, graph_mocks) -> None:
        graph_mocks["fetch"].side_effect = WhatsAppNetworkError("timeout")

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 502

    def test_falha_no_subscribe_retorna_400_sem_persistir(
        self, client, session, graph_mocks
    ) -> None:
        graph_mocks["subscribe"].side_effect = WhatsAppApiError("WABA não encontrada")

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 400
        assert response.json()["detail"] == "WABA não encontrada"
        session.commit.assert_not_awaited()

    def test_falha_de_rede_no_subscribe_retorna_502_sem_persistir(
        self, client, session, graph_mocks
    ) -> None:
        graph_mocks["subscribe"].side_effect = WhatsAppNetworkError("timeout")

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 502
        session.commit.assert_not_awaited()

    def test_numero_de_outro_tenant_retorna_409(self, client, session, graph_mocks) -> None:
        session.scalar.return_value = None
        session.commit.side_effect = IntegrityError("stmt", {}, Exception("unique"))

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 409

    def test_pin_invalido_retorna_422(self, client) -> None:
        body = {**CONNECT_BODY, "pin": "12a456"}

        response = client.post("/api/v1/whatsapp/connect", json=body)

        assert response.status_code == 422


class TestGetConnection:
    def test_sem_numero_conectado_retorna_null(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.get("/api/v1/whatsapp/connection")

        assert response.status_code == 200
        assert response.json() is None

    def test_numero_conectado_retorna_mascarado(self, client, session) -> None:
        session.scalar.return_value = _number()

        response = client.get("/api/v1/whatsapp/connection")

        assert response.status_code == 200
        body = response.json()
        assert body["display_phone_number"] == "+55 **** 4321"
        assert body["status"] == "connected"


class TestDisconnect:
    def test_desconecta_com_sucesso(self, client, session) -> None:
        existing = _number(status="connected")
        session.scalar.return_value = existing

        response = client.post("/api/v1/whatsapp/disconnect")

        assert response.status_code == 200
        assert existing.status == "disconnected"
        assert response.json()["status"] == "disconnected"

    def test_desconectar_sem_conexao_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post("/api/v1/whatsapp/disconnect")

        assert response.status_code == 404

    def test_desconecta_instancia_zapi_chama_a_z_api(self, client, session, monkeypatch) -> None:
        existing = _number(status="connected", provider="zapi")
        existing.zapi_instance_id = "inst-123"
        existing.zapi_instance_token_encrypted = "cifrado-token"
        session.scalar.return_value = existing
        disconnect_mock = AsyncMock(return_value=None)
        monkeypatch.setattr(whatsapp_module, "disconnect_zapi_instance", disconnect_mock)
        monkeypatch.setattr(
            whatsapp_module, "decrypt_access_token", MagicMock(return_value="token-claro")
        )

        response = client.post("/api/v1/whatsapp/disconnect")

        assert response.status_code == 200
        assert existing.status == "disconnected"
        disconnect_mock.assert_awaited_once_with("inst-123", "token-claro", None)

    def test_falha_ao_desconectar_na_z_api_ainda_desconecta_localmente(
        self, client, session, monkeypatch
    ) -> None:
        from app.clients.zapi import ZApiApiError

        existing = _number(status="connected", provider="zapi")
        existing.zapi_instance_id = "inst-123"
        existing.zapi_instance_token_encrypted = "cifrado-token"
        session.scalar.return_value = existing
        monkeypatch.setattr(
            whatsapp_module,
            "disconnect_zapi_instance",
            AsyncMock(side_effect=ZApiApiError("já desconectado")),
        )
        monkeypatch.setattr(
            whatsapp_module, "decrypt_access_token", MagicMock(return_value="token-claro")
        )

        response = client.post("/api/v1/whatsapp/disconnect")

        assert response.status_code == 200
        assert existing.status == "disconnected"


class TestWebhookConfig:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).get("/api/v1/whatsapp/webhook-config")
        assert response.status_code == 401

    def test_retorna_url_completa_e_verify_token(self, client, monkeypatch) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "api_public_url", "https://api.exemplo.com.br")
        monkeypatch.setattr(settings, "meta_verify_token", "meu-verify-token")

        response = client.get("/api/v1/whatsapp/webhook-config")

        assert response.status_code == 200
        assert response.json() == {
            "callback_url": "https://api.exemplo.com.br/api/v1/webhooks/whatsapp",
            "verify_token": "meu-verify-token",
        }

    def test_sem_api_public_url_degrada_pra_path_relativo(self, client, monkeypatch) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "api_public_url", "")
        monkeypatch.setattr(settings, "meta_verify_token", "meu-verify-token")

        response = client.get("/api/v1/whatsapp/webhook-config")

        assert response.status_code == 200
        assert response.json()["callback_url"] == "/api/v1/webhooks/whatsapp"
