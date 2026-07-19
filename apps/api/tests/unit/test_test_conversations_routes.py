import uuid
from decimal import Decimal
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
                "tokens_input": 2800,
                "tokens_output": 700,
                "current_agent": "agente_secretaria",
            }
        )
        monkeypatch.setattr(test_conversations_module.service, "send_playground_message", mock)
        pricing = SimpleNamespace(
            id=uuid.uuid4(),
            tokens_per_credit=1000,
            input_weight=Decimal("0.3"),
            output_weight=Decimal("1.0"),
        )
        monkeypatch.setattr(
            test_conversations_module.service,
            "get_current_pricing_config",
            AsyncMock(return_value=pricing),
        )
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
        # Último add é o lançamento do ledger — com os tokens brutos auditados.
        transaction = session.add.call_args.args[0]
        assert transaction.type == "consumption"
        # 2800*0.3 + 700*1.0 = 1540 tokens ponderados -> 1.54 créditos
        assert transaction.amount_credits == Decimal("-1.5400")
        assert transaction.tokens_input == 2800
        assert transaction.tokens_output == 700
        assert "token" not in transaction.description.lower()

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
