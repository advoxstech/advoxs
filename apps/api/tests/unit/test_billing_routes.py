import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.billing as billing_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.core.db import get_session
from app.main import app
from app.services.billing import InvalidPackageError, StripeApiError

TENANT_ID = uuid.uuid4()
PACKAGE_ID = uuid.uuid4()


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture
def client(session):
    async def override_tenant():
        return TenantContext(user_id=uuid.uuid4(), tenant_id=TENANT_ID, role="admin")

    async def override_session():
        yield session

    app.dependency_overrides[get_current_tenant] = override_tenant
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_tenant_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestBalance:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).get("/api/v1/billing/balance")
        assert response.status_code == 401

    def test_retorna_saldo_do_tenant_autenticado(self, client, session) -> None:
        session.get.return_value = MagicMock(credit_balance=1500)

        response = client.get("/api/v1/billing/balance")

        assert response.status_code == 200
        assert response.json() == {"credit_balance": 1500}


class TestCheckout:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).post(
            "/api/v1/billing/checkout", json={"credit_package_id": str(PACKAGE_ID)}
        )
        assert response.status_code == 401

    def test_sucesso_retorna_checkout_url(self, client, monkeypatch) -> None:
        create = AsyncMock(return_value="https://checkout.stripe.com/pay/cs_456")
        monkeypatch.setattr(billing_module, "create_recompra_checkout_session", create)

        response = client.post(
            "/api/v1/billing/checkout", json={"credit_package_id": str(PACKAGE_ID)}
        )

        assert response.status_code == 200
        assert response.json()["checkout_url"] == "https://checkout.stripe.com/pay/cs_456"
        args = create.call_args.args
        assert args[1] == TENANT_ID
        assert args[2] == PACKAGE_ID

    def test_pacote_invalido_retorna_400(self, client, monkeypatch) -> None:
        create = AsyncMock(side_effect=InvalidPackageError("pacote inválido"))
        monkeypatch.setattr(billing_module, "create_recompra_checkout_session", create)

        response = client.post(
            "/api/v1/billing/checkout", json={"credit_package_id": str(PACKAGE_ID)}
        )

        assert response.status_code == 400

    def test_falha_stripe_retorna_502(self, client, monkeypatch) -> None:
        create = AsyncMock(side_effect=StripeApiError("falhou"))
        monkeypatch.setattr(billing_module, "create_recompra_checkout_session", create)

        response = client.post(
            "/api/v1/billing/checkout", json={"credit_package_id": str(PACKAGE_ID)}
        )

        assert response.status_code == 502


class TestStatus:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).get("/api/v1/billing/status", params={"session_id": "cs_123"})
        assert response.status_code == 401

    def test_ready_quando_transacao_existe(self, client, session) -> None:
        session.scalar.return_value = uuid.uuid4()

        response = client.get("/api/v1/billing/status", params={"session_id": "cs_123"})

        assert response.status_code == 200
        assert response.json() == {"ready": True}

    def test_not_ready_quando_transacao_nao_existe(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.get("/api/v1/billing/status", params={"session_id": "cs_123"})

        assert response.json() == {"ready": False}

    def test_query_filtra_por_tenant_id(self, client, session) -> None:
        """Isolamento cross-tenant: a query precisa filtrar por tenant_id, não só
        por stripe_payment_id — caso contrário um tenant autenticado descobrindo o
        session_id de outro tenant conseguiria confirmar o pagamento dele."""
        session.scalar.return_value = uuid.uuid4()

        response = client.get("/api/v1/billing/status", params={"session_id": "cs_123"})

        assert response.status_code == 200
        query = session.scalar.call_args.args[0]
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
        assert "tenant_id" in compiled
        assert TENANT_ID.hex in compiled.replace("-", "")

    def test_not_ready_quando_transacao_e_de_outro_tenant(self, client, session) -> None:
        """Simula o filtro por tenant_id não encontrando a transação porque ela
        pertence a outro tenant (mesmo session_id, tenant diferente) — a rota deve
        responder ready=False, e não vazar que o pagamento existe para outro tenant."""
        session.scalar.return_value = None

        response = client.get("/api/v1/billing/status", params={"session_id": "cs_de_outro_tenant"})

        assert response.status_code == 200
        assert response.json() == {"ready": False}
