import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.whatsapp as whatsapp_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app

TENANT_ID = uuid.uuid4()


def _request(status: str = "pending") -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id=TENANT_ID,
        status=status,
        requested_at=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
    )


@pytest.fixture
def session():
    mock = AsyncMock()
    mock.add = MagicMock()

    async def fake_refresh(obj):
        # Simula os server_default de status/requested_at, preenchidos pelo
        # Postgres de verdade — aqui o commit/refresh são mocks, então nunca
        # rodam de fato (mesmo padrão de test_whatsapp_connection_routes.py).
        if getattr(obj, "status", None) is None:
            obj.status = "pending"
        if getattr(obj, "requested_at", None) is None:
            obj.requested_at = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)

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
def notify_mock(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(whatsapp_module, "send_zapi_request_notification", mock)
    return mock


class TestRequestManagedZApi:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).post("/api/v1/whatsapp/request-managed-zapi")
        assert response.status_code == 401

    def test_cria_pedido_quando_nao_existe_nenhum_pendente(
        self, client, session, notify_mock
    ) -> None:
        session.scalar.return_value = None
        session.get = AsyncMock(return_value=SimpleNamespace(name="Escritório X"))

        response = client.post("/api/v1/whatsapp/request-managed-zapi")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending"
        session.add.assert_called_once()
        session.commit.assert_awaited_once()
        notify_mock.assert_awaited_once()
        assert notify_mock.await_args.args[0] == "Escritório X"

    def test_criacao_sem_gmail_notify_mockado_nao_quebra_mesmo_sem_configuracao(
        self, client, session
    ) -> None:
        """Sem monkeypatch de send_zapi_request_notification (Gmail SMTP não
        configurado no ambiente de teste) — a rota precisa continuar
        registrando o pedido normalmente, sem propagar nenhum erro."""
        session.scalar.return_value = None
        session.get = AsyncMock(return_value=SimpleNamespace(name="Escritório X"))

        response = client.post("/api/v1/whatsapp/request-managed-zapi")

        assert response.status_code == 200
        assert response.json()["status"] == "pending"

    def test_idempotente_devolve_o_mesmo_pedido_pendente_sem_duplicar(
        self, client, session, notify_mock
    ) -> None:
        session.scalar.return_value = _request(status="pending")

        response = client.post("/api/v1/whatsapp/request-managed-zapi")

        assert response.status_code == 200
        assert response.json()["status"] == "pending"
        session.add.assert_not_called()
        session.commit.assert_not_awaited()
        notify_mock.assert_not_awaited()


class TestGetManagedZApiRequest:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).get("/api/v1/whatsapp/managed-zapi-request")
        assert response.status_code == 401

    def test_sem_pedido_retorna_null(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.get("/api/v1/whatsapp/managed-zapi-request")

        assert response.status_code == 200
        assert response.json() is None

    def test_com_pedido_pendente_retorna_status_e_data(self, client, session) -> None:
        session.scalar.return_value = _request(status="pending")

        response = client.get("/api/v1/whatsapp/managed-zapi-request")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending"
        assert body["requested_at"] == "2026-08-12T12:00:00Z"
