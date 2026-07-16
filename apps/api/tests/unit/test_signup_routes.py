import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.signup as signup_module
from app.core.db import get_system_session
from app.main import app
from app.services.billing import EmailAlreadyExistsError, InvalidPackageError, StripeApiError

PACKAGE_ID = uuid.uuid4()

CHECKOUT_BODY = {
    "tenant_name": "Escritório Teste",
    "email": "a@b.com",
    "password": "senha1234",
    "credit_package_id": str(PACKAGE_ID),
}


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture
def client(session):
    async def override_session():
        yield session

    app.dependency_overrides[get_system_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestCheckout:
    def test_sucesso_retorna_checkout_url(self, client, monkeypatch) -> None:
        create = AsyncMock(return_value="https://checkout.stripe.com/pay/cs_123")
        monkeypatch.setattr(signup_module, "create_checkout_session", create)

        response = client.post("/api/v1/signup/checkout", json=CHECKOUT_BODY)

        assert response.status_code == 200
        assert response.json()["checkout_url"] == "https://checkout.stripe.com/pay/cs_123"

    def test_email_duplicado_retorna_409(self, client, monkeypatch) -> None:
        create = AsyncMock(side_effect=EmailAlreadyExistsError("já cadastrado"))
        monkeypatch.setattr(signup_module, "create_checkout_session", create)

        response = client.post("/api/v1/signup/checkout", json=CHECKOUT_BODY)

        assert response.status_code == 409

    def test_pacote_invalido_retorna_400(self, client, monkeypatch) -> None:
        create = AsyncMock(side_effect=InvalidPackageError("pacote inválido"))
        monkeypatch.setattr(signup_module, "create_checkout_session", create)

        response = client.post("/api/v1/signup/checkout", json=CHECKOUT_BODY)

        assert response.status_code == 400

    def test_falha_stripe_retorna_502(self, client, monkeypatch) -> None:
        create = AsyncMock(side_effect=StripeApiError("falhou"))
        monkeypatch.setattr(signup_module, "create_checkout_session", create)

        response = client.post("/api/v1/signup/checkout", json=CHECKOUT_BODY)

        assert response.status_code == 502

    def test_senha_curta_retorna_422(self, client) -> None:
        body = {**CHECKOUT_BODY, "password": "curta"}

        response = client.post("/api/v1/signup/checkout", json=body)

        assert response.status_code == 422


class TestStatus:
    def test_ready_quando_transacao_existe(self, client, session, monkeypatch) -> None:
        redis = AsyncMock()
        redis.getdel.return_value = None
        monkeypatch.setattr(signup_module, "get_redis", AsyncMock(return_value=redis))
        session.scalar.return_value = uuid.uuid4()

        response = client.get("/api/v1/signup/status", params={"session_id": "cs_123"})

        assert response.status_code == 200
        assert response.json() == {"ready": True, "login_token": None}

    def test_not_ready_quando_transacao_nao_existe(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.get("/api/v1/signup/status", params={"session_id": "cs_123"})

        assert response.json() == {"ready": False, "login_token": None}

    def test_status_ready_entrega_login_token_uma_vez(self, client, session, monkeypatch) -> None:
        redis = AsyncMock()
        redis.getdel.return_value = "token-one-time"
        monkeypatch.setattr(signup_module, "get_redis", AsyncMock(return_value=redis))
        session.scalar.return_value = uuid.uuid4()  # transação encontrada → ready

        response = client.get("/api/v1/signup/status", params={"session_id": "cs_123"})

        assert response.status_code == 200
        assert response.json() == {"ready": True, "login_token": "token-one-time"}
        redis.getdel.assert_awaited_once_with("signup:handoff:cs_123")

    def test_status_nao_ready_nao_toca_no_redis(self, client, session, monkeypatch) -> None:
        redis = AsyncMock()
        monkeypatch.setattr(signup_module, "get_redis", AsyncMock(return_value=redis))
        session.scalar.return_value = None

        response = client.get("/api/v1/signup/status", params={"session_id": "cs_123"})

        assert response.json() == {"ready": False, "login_token": None}
        redis.getdel.assert_not_awaited()
