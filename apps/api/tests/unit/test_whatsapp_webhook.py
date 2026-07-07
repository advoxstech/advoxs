import hashlib
import hmac
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_session
from app.core.queue import get_arq_pool
from app.main import app

WEBHOOK_PATH = "/api/v1/webhooks/whatsapp"

TEXT_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "WABA_ID",
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "PNID"},
                        "messages": [
                            {
                                "from": "5511888888888",
                                "id": "wamid.ABC",
                                "type": "text",
                                "text": {"body": "Olá"},
                            }
                        ],
                    },
                }
            ],
        }
    ],
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

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_arq_pool] = override_arq
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestVerifyWebhook:
    def test_returns_challenge_with_valid_token(self, client) -> None:
        response = client.get(
            WEBHOOK_PATH,
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": settings.meta_verify_token,
                "hub.challenge": "12345",
            },
        )

        assert response.status_code == 200
        assert response.text == "12345"

    def test_rejects_invalid_token(self, client) -> None:
        response = client.get(
            WEBHOOK_PATH,
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "errado",
                "hub.challenge": "12345",
            },
        )

        assert response.status_code == 403


class TestReceiveWebhook:
    def test_persists_and_enqueues_message(self, client, fake_session, arq_pool) -> None:
        tenant_id = uuid.uuid4()
        number = MagicMock(tenant_id=tenant_id)
        conversation = MagicMock(id=uuid.uuid4(), tenant_id=tenant_id)
        # Ordem dos scalar(): número -> dedup (None) -> conversa existente
        fake_session.scalar.side_effect = [number, None, conversation]

        response = client.post(WEBHOOK_PATH, json=TEXT_PAYLOAD)

        assert response.status_code == 200
        assert response.json() == {"received": 1}
        fake_session.commit.assert_awaited_once()
        arq_pool.enqueue_job.assert_awaited_once()
        call = arq_pool.enqueue_job.await_args
        assert call.args == ("process_inbound_message",)
        assert call.kwargs["tenant_id"] == str(tenant_id)
        assert call.kwargs["conversation_id"] == str(conversation.id)

    def test_unknown_phone_number_id_is_ignored(self, client, fake_session, arq_pool) -> None:
        fake_session.scalar.side_effect = [None]

        response = client.post(WEBHOOK_PATH, json=TEXT_PAYLOAD)

        assert response.status_code == 200
        assert response.json() == {"received": 0}
        arq_pool.enqueue_job.assert_not_awaited()

    def test_duplicate_wamid_is_ignored(self, client, fake_session, arq_pool) -> None:
        number = MagicMock(tenant_id=uuid.uuid4())
        fake_session.scalar.side_effect = [number, uuid.uuid4()]  # dedup encontra mensagem

        response = client.post(WEBHOOK_PATH, json=TEXT_PAYLOAD)

        assert response.status_code == 200
        assert response.json() == {"received": 0}
        arq_pool.enqueue_job.assert_not_awaited()

    def test_status_only_payload_returns_zero(self, client, arq_pool) -> None:
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "PNID"},
                                "statuses": [{"id": "wamid.X", "status": "read"}],
                            },
                        }
                    ]
                }
            ]
        }

        response = client.post(WEBHOOK_PATH, json=payload)

        assert response.status_code == 200
        assert response.json() == {"received": 0}
        arq_pool.enqueue_job.assert_not_awaited()

    def test_invalid_json_returns_400(self, client) -> None:
        response = client.post(
            WEBHOOK_PATH, content=b"nao-e-json", headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 400


class TestSignatureValidation:
    @pytest.fixture(autouse=True)
    def app_secret(self, monkeypatch):
        monkeypatch.setattr(settings, "meta_app_secret", "app-secret")

    def test_valid_signature_is_accepted(self, client, fake_session) -> None:
        fake_session.scalar.side_effect = [None]
        body = json.dumps(TEXT_PAYLOAD).encode()
        signature = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()

        response = client.post(
            WEBHOOK_PATH,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": f"sha256={signature}",
            },
        )

        assert response.status_code == 200

    def test_invalid_signature_is_rejected(self, client) -> None:
        response = client.post(
            WEBHOOK_PATH,
            json=TEXT_PAYLOAD,
            headers={"X-Hub-Signature-256": "sha256=deadbeef"},
        )

        assert response.status_code == 403

    def test_missing_signature_is_rejected(self, client) -> None:
        response = client.post(WEBHOOK_PATH, json=TEXT_PAYLOAD)

        assert response.status_code == 403
