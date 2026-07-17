import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app

TENANT_ID = uuid.uuid4()


@pytest.fixture
def session():
    return AsyncMock()


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


def _execute_result(rows: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


class TestConversationsUsage:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).get(
            "/api/v1/conversations/usage", params={"from": "2026-07-01", "to": "2026-07-17"}
        )
        assert response.status_code == 401

    def test_agrega_por_conversa_ordenado_por_credito_desc(self, client, session) -> None:
        conv_a = uuid.uuid4()
        conv_b = uuid.uuid4()
        session.execute.return_value = _execute_result(
            [
                (conv_a, "5511999990001", False, 12.5, 3, datetime(2026, 7, 15, tzinfo=UTC)),
                (conv_b, "teste-abc123def456", True, 2.0, 1, datetime(2026, 7, 10, tzinfo=UTC)),
            ]
        )

        response = client.get(
            "/api/v1/conversations/usage", params={"from": "2026-07-01", "to": "2026-07-17"}
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        assert body[0]["conversation_id"] == str(conv_a)
        assert body[0]["credits_consumed"] == 12.5
        assert body[0]["billed_responses"] == 3
        assert body[1]["is_test"] is True

    def test_to_anterior_a_from_retorna_422(self, client) -> None:
        response = client.get(
            "/api/v1/conversations/usage", params={"from": "2026-07-17", "to": "2026-07-01"}
        )
        assert response.status_code == 422

    def test_sem_datas_retorna_422(self, client) -> None:
        response = client.get("/api/v1/conversations/usage")
        assert response.status_code == 422
