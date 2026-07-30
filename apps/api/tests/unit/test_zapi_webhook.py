import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_system_session
from app.core.queue import get_arq_pool
from app.main import app
from app.models import Message

WEBHOOK_SECRET = "segredo-abc"
WEBHOOK_PATH = f"/api/v1/webhooks/zapi/{WEBHOOK_SECRET}"

TEXT_PAYLOAD = {
    "instanceId": "inst-123",
    "phone": "5511888888888",
    "messageId": "msg-abc",
    "fromMe": False,
    "text": {"message": "Olá"},
}

IMAGE_PAYLOAD = {
    "instanceId": "inst-123",
    "phone": "5511888888888",
    "messageId": "msg-img-1",
    "fromMe": False,
    "text": None,
    "image": {
        "mimeType": "image/jpeg",
        "imageUrl": "https://z-api.example/media/foto.jpg",
        "caption": "Segue o documento",
    },
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

    def test_from_me_e_ignorado(self, client, fake_session, arq_pool) -> None:
        payload = {**TEXT_PAYLOAD, "fromMe": True}

        response = client.post(WEBHOOK_PATH, json=payload)

        assert response.status_code == 200
        assert response.json() == {"received": 0}
        arq_pool.enqueue_job.assert_not_awaited()
        # fromMe=true precisa ser filtrado ANTES de qualquer consulta ao
        # banco (extract_inbound_zapi_message retorna None de cara) — sem
        # essa asserção, um bug que movesse o filtro pra depois do lookup de
        # zapi_instance_id passaria despercebido.
        fake_session.scalar.assert_not_called()

    def test_persiste_mensagem_de_imagem_com_media_url(
        self, client, fake_session, arq_pool
    ) -> None:
        tenant_id = uuid.uuid4()
        number = MagicMock(tenant_id=tenant_id, zapi_webhook_secret=WEBHOOK_SECRET)
        conversation = MagicMock(id=uuid.uuid4(), tenant_id=tenant_id)
        fake_session.scalar.side_effect = [number, None, conversation]

        response = client.post(WEBHOOK_PATH, json=IMAGE_PAYLOAD)

        assert response.status_code == 200
        assert response.json() == {"received": 1}
        messages = [
            call.args[0] for call in fake_session.add.call_args_list
            if isinstance(call.args[0], Message)
        ]
        assert len(messages) == 1
        assert messages[0].media_url == "https://z-api.example/media/foto.jpg"
        assert messages[0].media_type == "image/jpeg"
        assert messages[0].content == "Segue o documento"
