import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.test_conversations as test_conversations_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.agents import AgentsNetworkError
from app.main import app

TENANT_ID = uuid.uuid4()
CONVERSATION_ID = uuid.uuid4()


def _conversation(is_test: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        contact_phone_number="teste-abc123def456",
        state="agent",
        is_test=is_test,
        last_message_at=None,
        created_at=__import__("datetime").datetime(2026, 7, 16, tzinfo=__import__("datetime").UTC),
        summary=None,
        summary_generated_at=None,
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


class TestCreate:
    def test_cria_conversa_de_teste(self, client, session) -> None:
        async def fake_refresh(obj):
            obj.id = CONVERSATION_ID
            obj.state = "agent"
            obj.created_at = _conversation().created_at
            obj.last_message_at = None
            obj.summary = None
            obj.summary_generated_at = None

        session.refresh.side_effect = fake_refresh

        response = client.post("/api/v1/test-conversations")

        assert response.status_code == 201
        body = response.json()
        assert body["is_test"] is True
        assert body["contact_phone_number"].startswith("teste-")
        session.add.assert_called_once()
        session.commit.assert_awaited()


class TestSendTestMessage:
    @pytest.fixture
    def playground_mock(self, monkeypatch):
        mock = AsyncMock(
            return_value={
                "responses": ["resposta 1", "resposta 2"],
                "tokens_used": 3500,
                "current_agent": "agente_secretaria",
            }
        )
        monkeypatch.setattr(test_conversations_module.service, "send_playground_message", mock)
        return mock

    def _arm_session(self, session, conversation, balance=1000):
        # scalar: 1ª chamada resolve a conversa; get: tenant com saldo
        session.scalar.return_value = conversation
        session.get.return_value = SimpleNamespace(id=TENANT_ID, credit_balance=balance)

        async def fake_refresh(obj):
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if getattr(obj, "created_at", None) is None:
                obj.created_at = conversation.created_at
            for campo in ("media_url", "media_type", "delivery_status"):
                if not hasattr(obj, campo):
                    setattr(obj, campo, None)

        session.refresh.side_effect = fake_refresh

    def test_fluxo_feliz_persiste_e_debita(self, client, session, playground_mock) -> None:
        self._arm_session(session, _conversation())

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            json={"content": "olá, quero saber sobre condomínio"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["grouped"] is False
        assert len(body["messages"]) == 3  # contato + 2 respostas
        assert body["messages"][0]["sender_type"] == "contact"
        assert body["messages"][1]["sender_type"] == "agent"
        playground_mock.assert_awaited_once()
        assert playground_mock.await_args.kwargs["contact_phone_number"] == "teste-abc123def456"

    def test_conversa_real_retorna_409(self, client, session, playground_mock) -> None:
        self._arm_session(session, _conversation(is_test=False))

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            json={"content": "oi"},
        )

        assert response.status_code == 409
        playground_mock.assert_not_awaited()

    def test_sem_saldo_retorna_402(self, client, session, playground_mock) -> None:
        self._arm_session(session, _conversation(), balance=0)

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            json={"content": "oi"},
        )

        assert response.status_code == 402
        playground_mock.assert_not_awaited()

    def test_grouped_nao_persiste_resposta(self, client, session, playground_mock) -> None:
        playground_mock.return_value = None
        self._arm_session(session, _conversation())

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            json={"content": "oi"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["grouped"] is True
        assert len(body["messages"]) == 1  # só a do contato

    def test_falha_do_agents_retorna_502(self, client, session, playground_mock) -> None:
        playground_mock.side_effect = AgentsNetworkError("fora do ar")
        self._arm_session(session, _conversation())

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            json={"content": "oi"},
        )

        assert response.status_code == 502
        # a mensagem do contato foi commitada antes da chamada
        session.commit.assert_awaited()

    def test_conversa_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            json={"content": "oi"},
        )

        assert response.status_code == 404


class TestDelete:
    def test_apaga_conversa_de_teste(self, client, session, monkeypatch) -> None:
        cleanup_mock = AsyncMock()
        monkeypatch.setattr(
            test_conversations_module.service, "delete_playground_conversation", cleanup_mock
        )
        session.scalar.return_value = _conversation()

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 204
        session.delete.assert_awaited_once()
        session.commit.assert_awaited()
        cleanup_mock.assert_awaited_once_with(f"{TENANT_ID}:teste-abc123def456")

    def test_conversa_real_retorna_409(self, client, session, monkeypatch) -> None:
        cleanup_mock = AsyncMock()
        monkeypatch.setattr(
            test_conversations_module.service, "delete_playground_conversation", cleanup_mock
        )
        session.scalar.return_value = _conversation(is_test=False)

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 409
        session.delete.assert_not_awaited()
        cleanup_mock.assert_not_awaited()

    def test_desvincula_ledger_antes_de_apagar(self, client, session, monkeypatch) -> None:
        monkeypatch.setattr(
            test_conversations_module.service,
            "delete_playground_conversation",
            AsyncMock(),
        )
        session.scalar.return_value = _conversation()

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 204
        # dois executes: UPDATE credit_transactions (related_message_id=NULL)
        # e DELETE messages, nessa ordem
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        update_idx = next(i for i, s in enumerate(statements) if "credit_transactions" in s)
        delete_idx = next(i for i, s in enumerate(statements) if "DELETE FROM messages" in s)
        assert update_idx < delete_idx
