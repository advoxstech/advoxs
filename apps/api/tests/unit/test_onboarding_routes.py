import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app

TENANT_ID = uuid.uuid4()


def _tenant(completed_at=None, credit_balance="0") -> SimpleNamespace:
    return SimpleNamespace(
        id=TENANT_ID,
        onboarding_completed_at=completed_at,
        credit_balance=Decimal(credit_balance),
    )


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


def test_get_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/onboarding")
    assert response.status_code == 401


class TestGetOnboarding:
    def test_retorna_progresso_real_sem_marcar_tutorial_completo(self, client, session) -> None:
        session.get.return_value = _tenant(completed_at=None, credit_balance="20")
        session.scalar.side_effect = [uuid.uuid4(), None, uuid.uuid4(), None, None]

        response = client.get("/api/v1/onboarding")

        assert response.status_code == 200
        body = response.json()
        assert body["completed"] is False
        assert body["main_configuration_complete"] is False
        assert body["completed_steps"] == 3
        assert body["total_steps"] == 6
        assert {step["key"]: step["completed"] for step in body["steps"]} == {
            "agent": True,
            "whatsapp": False,
            "credits": True,
            "knowledge_base": True,
            "test_conversation": False,
            "first_attendance": False,
        }

    def test_configuracao_principal_pronta_independe_do_lembrete(self, client, session) -> None:
        session.get.return_value = _tenant(
            completed_at=datetime(2026, 7, 16, tzinfo=UTC),
            credit_balance="1",
        )
        session.scalar.side_effect = [uuid.uuid4()] * 5

        response = client.get("/api/v1/onboarding")

        body = response.json()
        assert body["completed"] is True
        assert body["main_configuration_complete"] is True
        assert body["completed_steps"] == 6


class TestCompleteOnboarding:
    def test_seta_timestamp_e_retorna_204(self, client, session) -> None:
        tenant = _tenant(completed_at=None)
        session.get.return_value = tenant

        response = client.post("/api/v1/onboarding/complete")

        assert response.status_code == 204
        assert tenant.onboarding_completed_at is not None
        session.commit.assert_awaited()

    def test_idempotente_nao_altera_timestamp_original(self, client, session) -> None:
        original = datetime(2026, 7, 1, tzinfo=UTC)
        tenant = _tenant(completed_at=original)
        session.get.return_value = tenant

        response = client.post("/api/v1/onboarding/complete")

        assert response.status_code == 204
        assert tenant.onboarding_completed_at == original
