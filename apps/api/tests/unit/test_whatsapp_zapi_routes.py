import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.whatsapp as whatsapp_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.zapi import ZApiApiError, ZApiNetworkError
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
def zapi_mocks(monkeypatch):
    mocks = {
        "check_status": AsyncMock(return_value={"connected": False}),
        "configure_webhook": AsyncMock(return_value=None),
        "encrypt": MagicMock(side_effect=lambda v: f"cifrado:{v}"),
    }
    monkeypatch.setattr(whatsapp_module, "check_zapi_status", mocks["check_status"])
    monkeypatch.setattr(whatsapp_module, "configure_zapi_webhook", mocks["configure_webhook"])
    monkeypatch.setattr(whatsapp_module, "encrypt_access_token", mocks["encrypt"])
    monkeypatch.setattr(whatsapp_module.settings, "api_public_url", "https://api.exemplo.com.br")
    return mocks


class TestConnectZApi:
    def test_conexao_feliz(self, client, session, zapi_mocks) -> None:
        session.scalar.return_value = None

        response = client.post("/api/v1/whatsapp/connect-zapi", json=CONNECT_ZAPI_BODY)

        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "zapi"
        assert body["status"] == "disconnected"
        session.add.assert_called_once()
        zapi_mocks["check_status"].assert_awaited_once_with(
            "inst-123", "token-claro", "client-token-claro"
        )
        zapi_mocks["configure_webhook"].assert_awaited_once()
        webhook_url_arg = zapi_mocks["configure_webhook"].await_args.args[3]
        assert webhook_url_arg.startswith("https://api.exemplo.com.br/api/v1/webhooks/zapi/")

    def test_credenciais_invalidas_retorna_400_sem_persistir(
        self, client, session, zapi_mocks
    ) -> None:
        zapi_mocks["check_status"].side_effect = ZApiApiError("credenciais inválidas")

        response = client.post("/api/v1/whatsapp/connect-zapi", json=CONNECT_ZAPI_BODY)

        assert response.status_code == 400
        session.commit.assert_not_awaited()

    def test_falha_de_rede_retorna_502(self, client, session, zapi_mocks) -> None:
        zapi_mocks["check_status"].side_effect = ZApiNetworkError("timeout")

        response = client.post("/api/v1/whatsapp/connect-zapi", json=CONNECT_ZAPI_BODY)

        assert response.status_code == 502

    def test_falha_ao_configurar_webhook_retorna_400_sem_persistir(
        self, client, session, zapi_mocks
    ) -> None:
        zapi_mocks["configure_webhook"].side_effect = ZApiApiError("falha ao configurar")

        response = client.post("/api/v1/whatsapp/connect-zapi", json=CONNECT_ZAPI_BODY)

        assert response.status_code == 400
        session.commit.assert_not_awaited()

    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).post("/api/v1/whatsapp/connect-zapi", json=CONNECT_ZAPI_BODY)
        assert response.status_code == 401


def _zapi_number(status: str = "disconnected") -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id=TENANT_ID,
        provider="zapi",
        zapi_instance_id="inst-123",
        zapi_instance_token_encrypted="cifrado:token-claro",
        zapi_client_token_encrypted="cifrado:client-token-claro",
        display_phone_number="Aguardando pareamento",
        status=status,
        connected_at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
    )


class TestZApiStatus:
    def test_falha_ao_buscar_telefone_degrada_para_estado_salvo(
        self, client, session, monkeypatch
    ) -> None:
        """Regressão: fetch_zapi_connected_phone (parte do self-heal) também
        precisa degradar pro estado já salvo em caso de falha — só envolver
        check_zapi_status no try/except não bastava, já que o /device pode
        falhar mesmo com o /status funcionando."""
        number = _zapi_number(status="disconnected")
        session.scalar.return_value = number

        monkeypatch.setattr(
            whatsapp_module, "decrypt_access_token", lambda v: v.replace("cifrado:", "")
        )
        monkeypatch.setattr(
            whatsapp_module, "check_zapi_status", AsyncMock(return_value={"connected": True})
        )
        monkeypatch.setattr(
            whatsapp_module,
            "fetch_zapi_connected_phone",
            AsyncMock(side_effect=ZApiApiError("dispositivo instável")),
        )

        response = client.get("/api/v1/whatsapp/zapi-status")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "disconnected"
        session.commit.assert_not_awaited()
