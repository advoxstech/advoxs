import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.clients.agents import AgentsApiError, AgentsNetworkError
from app.services.playground import TenantNotFoundError, delete_conversation, send_message

TENANT_ID = uuid.uuid4()

AGENTS_PAYLOAD = [
    {
        "id": "a1",
        "name": "Secretária",
        "instructions": "x",
        "is_entry_point": True,
        "knowledge_base_file_ids": [],
    }
]


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture(autouse=True)
def load_agents_mock(monkeypatch):
    mock = AsyncMock(return_value=AGENTS_PAYLOAD)
    monkeypatch.setattr("app.services.playground.load_agents_for_engine", mock)
    return mock


class TestSendMessage:
    async def test_tenant_inexistente_levanta_tenant_not_found(
        self, session, monkeypatch, load_agents_mock
    ):
        session.get.return_value = None
        client_mock = AsyncMock()
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        with pytest.raises(TenantNotFoundError):
            await send_message(session, TENANT_ID, "sess-1", "olá")

        client_mock.assert_not_awaited()
        load_agents_mock.assert_not_awaited()

    async def test_resposta_normal_retorna_dados_do_agente(
        self, session, monkeypatch, load_agents_mock
    ):
        session.get.return_value = SimpleNamespace(id=TENANT_ID)
        client_mock = AsyncMock(
            return_value={
                "responses": ["oi, como posso ajudar?"],
                "tokens_used": 321,
                "current_agent": "Secretária",
            }
        )
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        result = await send_message(session, TENANT_ID, "sess-1", "olá")

        assert result.responses == ["oi, como posso ajudar?"]
        assert result.tokens_used == 321
        assert result.current_agent == "Secretária"
        assert result.grouped is False
        client_mock.assert_awaited_once_with(
            tenant_id=str(TENANT_ID),
            contact_phone_number="playground-sess-1",
            message="olá",
            agents=AGENTS_PAYLOAD,
        )

    async def test_debounce_agrupou_retorna_grouped_true(
        self, session, monkeypatch, load_agents_mock
    ):
        session.get.return_value = SimpleNamespace(id=TENANT_ID)
        client_mock = AsyncMock(return_value=None)
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        result = await send_message(session, TENANT_ID, "sess-1", "olá")

        assert result.grouped is True
        assert result.responses == []
        assert result.tokens_used is None
        assert result.current_agent is None

    async def test_erro_do_agents_propaga(self, session, monkeypatch, load_agents_mock):
        session.get.return_value = SimpleNamespace(id=TENANT_ID)
        client_mock = AsyncMock(side_effect=AgentsApiError("HTTP 500"))
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        with pytest.raises(AgentsApiError):
            await send_message(session, TENANT_ID, "sess-1", "olá")

    async def test_erro_de_rede_propaga(self, session, monkeypatch, load_agents_mock):
        session.get.return_value = SimpleNamespace(id=TENANT_ID)
        client_mock = AsyncMock(side_effect=AgentsNetworkError("timeout"))
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        with pytest.raises(AgentsNetworkError):
            await send_message(session, TENANT_ID, "sess-1", "olá")


class TestDeleteConversation:
    async def test_monta_thread_id_com_prefixo_playground(self, monkeypatch):
        delete_mock = AsyncMock()
        monkeypatch.setattr("app.services.playground.delete_agent_checkpoint", delete_mock)

        await delete_conversation(TENANT_ID, "sess-1")

        delete_mock.assert_awaited_once_with(f"{TENANT_ID}:playground-sess-1")
