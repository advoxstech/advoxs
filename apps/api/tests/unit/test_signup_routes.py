import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
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
    @staticmethod
    def _verified(monkeypatch):
        pending = SimpleNamespace(
            stripe_checkout_url=None,
            stripe_checkout_created_at=None,
            stripe_checkout_id=None,
        )
        monkeypatch.setattr(signup_module, "get_verified_signup", AsyncMock(return_value=pending))
        return pending

    def test_sucesso_retorna_checkout_url(self, client, monkeypatch) -> None:
        pending = self._verified(monkeypatch)
        create = AsyncMock(return_value=("cs_123", "https://checkout.stripe.com/pay/cs_123"))
        monkeypatch.setattr(signup_module, "create_verified_checkout_session", create)

        response = client.post("/api/v1/signup/checkout", json={"checkout_token": "confirmed"})

        assert response.status_code == 200
        assert response.json()["checkout_url"] == "https://checkout.stripe.com/pay/cs_123"
        assert pending.stripe_checkout_id == "cs_123"

    def test_email_duplicado_retorna_409(self, client, monkeypatch) -> None:
        self._verified(monkeypatch)
        create = AsyncMock(side_effect=EmailAlreadyExistsError("já cadastrado"))
        monkeypatch.setattr(signup_module, "create_verified_checkout_session", create)

        response = client.post("/api/v1/signup/checkout", json={"checkout_token": "confirmed"})

        assert response.status_code == 409

    def test_pacote_invalido_retorna_400(self, client, monkeypatch) -> None:
        self._verified(monkeypatch)
        create = AsyncMock(side_effect=InvalidPackageError("pacote inválido"))
        monkeypatch.setattr(signup_module, "create_verified_checkout_session", create)

        response = client.post("/api/v1/signup/checkout", json={"checkout_token": "confirmed"})

        assert response.status_code == 400

    def test_falha_stripe_retorna_502(self, client, monkeypatch) -> None:
        self._verified(monkeypatch)
        create = AsyncMock(side_effect=StripeApiError("falhou"))
        monkeypatch.setattr(signup_module, "create_verified_checkout_session", create)

        response = client.post("/api/v1/signup/checkout", json={"checkout_token": "confirmed"})

        assert response.status_code == 502

    def test_senha_curta_retorna_422(self, client) -> None:
        body = {**CHECKOUT_BODY, "password": "curta"}

        response = client.post("/api/v1/signup/request-verification", json=body)

        assert response.status_code == 422

    def test_checkout_sem_confirmacao_retorna_422(self, client) -> None:
        response = client.post("/api/v1/signup/checkout", json=CHECKOUT_BODY)
        assert response.status_code == 422

    def test_checkout_reutiliza_sessao(self, client, monkeypatch) -> None:
        pending = self._verified(monkeypatch)
        pending.stripe_checkout_url = "https://checkout.stripe.com/pay/cs_123"
        pending.stripe_checkout_created_at = datetime.now(UTC)
        create = AsyncMock()
        monkeypatch.setattr(signup_module, "create_verified_checkout_session", create)
        response = client.post("/api/v1/signup/checkout", json={"checkout_token": "confirmed"})
        assert response.status_code == 200
        create.assert_not_awaited()


class TestVerification:
    def test_solicita_confirmacao_antes_do_checkout(self, client, monkeypatch) -> None:
        request = AsyncMock()
        monkeypatch.setattr(signup_module, "request_verification", request)
        response = client.post("/api/v1/signup/request-verification", json=CHECKOUT_BODY)
        assert response.status_code == 202
        request.assert_awaited_once()

    def test_confirmacao_entrega_credencial_de_checkout(self, client, monkeypatch) -> None:
        verify = AsyncMock(return_value="checkout-secret")
        monkeypatch.setattr(signup_module, "verify_email", verify)
        response = client.post("/api/v1/signup/verify-email", json={"token": "email-secret"})
        assert response.json() == {"checkout_token": "checkout-secret"}


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
