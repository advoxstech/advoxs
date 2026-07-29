import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_system_session
from app.core.queue import get_arq_pool
from app.main import app

WEBHOOK_SECRET = "segredo-abc"
WEBHOOK_PATH = f"/api/v1/webhooks/zapi/{WEBHOOK_SECRET}"

TEXT_PAYLOAD = {
    "instanceId": "inst-123",
    "phone": "5511888888888",
    "messageId": "msg-abc",
    "fromMe": False,
    "text": {"message": "Olá"},
}


@pytest.fixture
def arq_pool():
    return AsyncMock()


@pytest.fixture
def fake_session():
    session = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def client(fake_session, arq_pool):
    async def override_session():
        yield fake_session

    async def override_arq():
        return arq_pool

    app.dependency_overrides[get_system_session] = override_session
    app.dependency_overrides[get_arq_pool] = override_arq
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestReceiveZApiWebhook:
    def test_persiste_e_enfileira_com_segredo_correto(self, client, fake_session, arq_pool) -> None:
        tenant_id = uuid.uuid4()
        number = MagicMock(tenant_id=tenant_id, zapi_webhook_secret=WEBHOOK_SECRET)
        conversation = MagicMock(id=uuid.uuid4(), tenant_id=tenant_id)
        fake_session.scalar.side_effect = [number, None, conversation]

        response = client.post(WEBHOOK_PATH, json=TEXT_PAYLOAD)

        assert response.status_code == 200
        assert response.json() == {"received": 1}
        arq_pool.enqueue_job.assert_awaited_once()

    def test_segredo_errado_e_ignorado(self, client, fake_session, arq_pool) -> None:
        number = MagicMock(zapi_webhook_secret="outro-segredo-diferente")
        fake_session.scalar.side_effect = [number]

        response = client.post(WEBHOOK_PATH, json=TEXT_PAYLOAD)

        assert response.status_code == 200
        assert response.json() == {"received": 0}
        arq_pool.enqueue_job.assert_not_awaited()

    def test_instancia_desconhecida_e_ignorada(self, client, fake_session, arq_pool) -> None:
        fake_session.scalar.side_effect = [None]

        response = client.post(WEBHOOK_PATH, json=TEXT_PAYLOAD)

        assert response.status_code == 200
        assert response.json() == {"received": 0}
        arq_pool.enqueue_job.assert_not_awaited()

    def test_from_me_e_ignorado(self, client, arq_pool) -> None:
        payload = {**TEXT_PAYLOAD, "fromMe": True}

        response = client.post(WEBHOOK_PATH, json=payload)

        assert response.status_code == 200
        assert response.json() == {"received": 0}
        arq_pool.enqueue_job.assert_not_awaited()
