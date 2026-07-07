import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.conversations as conversations_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.whatsapp import WhatsAppSendError
from app.main import app

TENANT_ID = uuid.uuid4()
CONVERSATION_ID = uuid.uuid4()


def _conversation(state: str = "agent") -> SimpleNamespace:
    return SimpleNamespace(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        contact_phone_number="5511999998888",
        state=state,
        last_message_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )


def _number() -> SimpleNamespace:
    return SimpleNamespace(
        phone_number_id="PNID",
        access_token_encrypted="token-cifrado",
        status="connected",
    )


@pytest.fixture
def session():
    mock = AsyncMock()

    async def fake_refresh(obj):
        obj.id = uuid.uuid4()
        obj.created_at = datetime.now(UTC)

    mock.add = MagicMock()
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
def whatsapp_send(monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(conversations_module, "send_text_message", send)
    monkeypatch.setattr(
        conversations_module, "decrypt_access_token", MagicMock(return_value="token-claro")
    )
    return send


def _execute_returning(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/conversations")

    assert response.status_code == 401


class TestListConversations:
    def test_lista_conversas_do_tenant(self, client, session) -> None:
        session.execute.return_value = _execute_returning([_conversation()])

        response = client.get("/api/v1/conversations")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["id"] == str(CONVERSATION_ID)
        assert body[0]["state"] == "agent"


class TestListMessages:
    def test_lista_mensagens(self, client, session) -> None:
        session.scalar.return_value = _conversation()
        message = SimpleNamespace(
            id=uuid.uuid4(),
            sender_type="contact",
            content="Olá",
            media_url=None,
            media_type=None,
            created_at=datetime.now(UTC),
        )
        session.execute.return_value = _execute_returning([message])

        response = client.get(f"/api/v1/conversations/{CONVERSATION_ID}/messages")

        assert response.status_code == 200
        assert response.json()[0]["content"] == "Olá"

    def test_conversa_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.get(f"/api/v1/conversations/{uuid.uuid4()}/messages")

        assert response.status_code == 404


class TestTakeover:
    def test_altera_estado_para_human(self, client, session) -> None:
        conversation = _conversation(state="agent")
        session.scalar.return_value = conversation

        response = client.patch(f"/api/v1/conversations/{CONVERSATION_ID}", json={"state": "human"})

        assert response.status_code == 200
        assert conversation.state == "human"
        session.commit.assert_awaited_once()

    def test_estado_invalido_retorna_422(self, client) -> None:
        response = client.patch(f"/api/v1/conversations/{CONVERSATION_ID}", json={"state": "robo"})

        assert response.status_code == 422


class TestSendMessage:
    def test_envia_e_persiste_como_human(self, client, session, whatsapp_send) -> None:
        session.scalar.side_effect = [_conversation(state="human"), _number()]

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages",
            json={"content": "Bom dia, aqui é o advogado"},
        )

        assert response.status_code == 201
        assert response.json()["sender_type"] == "human"
        whatsapp_send.assert_awaited_once_with(
            phone_number_id="PNID",
            access_token="token-claro",
            to="5511999998888",
            text="Bom dia, aqui é o advogado",
        )
        persisted = session.add.call_args.args[0]
        assert persisted.sender_type == "human"
        assert persisted.tenant_id == TENANT_ID
        session.commit.assert_awaited_once()

    def test_conversa_em_modo_agente_retorna_409(self, client, session, whatsapp_send) -> None:
        session.scalar.side_effect = [_conversation(state="agent")]

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages", json={"content": "oi"}
        )

        assert response.status_code == 409
        whatsapp_send.assert_not_awaited()

    def test_sem_numero_conectado_retorna_409(self, client, session, whatsapp_send) -> None:
        session.scalar.side_effect = [_conversation(state="human"), None]

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages", json={"content": "oi"}
        )

        assert response.status_code == 409
        whatsapp_send.assert_not_awaited()

    def test_falha_na_graph_api_retorna_502(self, client, session, whatsapp_send) -> None:
        session.scalar.side_effect = [_conversation(state="human"), _number()]
        whatsapp_send.side_effect = WhatsAppSendError("HTTP 500")

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages", json={"content": "oi"}
        )

        assert response.status_code == 502
        session.add.assert_not_called()

    def test_conteudo_vazio_retorna_422(self, client) -> None:
        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages", json={"content": ""}
        )

        assert response.status_code == 422
