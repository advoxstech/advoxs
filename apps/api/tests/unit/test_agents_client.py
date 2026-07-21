from unittest.mock import AsyncMock

import httpx
import pytest

from app.clients.agents import (
    AgentsApiError,
    AgentsNetworkError,
    generate_conversation_summary,
    send_playground_message,
)


class TestGenerateConversationSummary:
    async def test_retorna_resumo_e_tokens(self, monkeypatch) -> None:
        response = httpx.Response(
            200,
            json={
                "summary": "Resumo da conversa.",
                "tokens_used": 88,
                "tokens_input": 60,
                "tokens_output": 28,
            },
        )
        mock_post = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        result = await generate_conversation_summary([{"sender_type": "contact", "content": "oi"}])

        assert result == {
            "summary": "Resumo da conversa.",
            "tokens_used": 88,
            "tokens_input": 60,
            "tokens_output": 28,
        }
        mock_post.assert_awaited_once()
        assert mock_post.call_args.args[0] == "/summaries"
        assert mock_post.call_args.kwargs["json"] == {
            "messages": [{"sender_type": "contact", "content": "oi"}]
        }

    async def test_resposta_sem_breakdown_de_tokens_usa_zero(self, monkeypatch) -> None:
        # agents antigo (sem tokens_input/tokens_output) durante o deploy.
        response = httpx.Response(200, json={"summary": "Resumo.", "tokens_used": 88})
        monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))

        result = await generate_conversation_summary([{"sender_type": "contact", "content": "oi"}])

        assert result == {
            "summary": "Resumo.",
            "tokens_used": 88,
            "tokens_input": 0,
            "tokens_output": 0,
        }

    async def test_erro_http_levanta_agents_api_error(self, monkeypatch) -> None:
        response = httpx.Response(500, text="erro interno")
        monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))

        with pytest.raises(AgentsApiError):
            await generate_conversation_summary([{"sender_type": "contact", "content": "oi"}])

    async def test_falha_de_rede_levanta_agents_network_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            httpx.AsyncClient,
            "post",
            AsyncMock(side_effect=httpx.ConnectError("conexão recusada")),
        )

        with pytest.raises(AgentsNetworkError):
            await generate_conversation_summary([{"sender_type": "contact", "content": "oi"}])

    async def test_resposta_sem_summary_levanta_agents_api_error(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"tokens_used": 10})
        monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))

        with pytest.raises(AgentsApiError):
            await generate_conversation_summary([{"sender_type": "contact", "content": "oi"}])


class TestSendPlaygroundMessage:
    async def test_inclui_agents_quando_informado(self, monkeypatch) -> None:
        response = httpx.Response(
            200, json={"responses": ["oi"], "tokens_used": 0, "current_agent": None}
        )
        mock_post = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
        agents = [
            {
                "id": "a1",
                "name": "Secretária",
                "instructions": "x",
                "is_entry_point": True,
                "knowledge_base_file_ids": [],
            }
        ]

        await send_playground_message(
            tenant_id="t1", contact_phone_number="playground-s1", message="oi", agents=agents
        )

        body = mock_post.call_args.kwargs["json"]
        assert body["agents"] == agents

    async def test_sem_agents_manda_lista_vazia(self, monkeypatch) -> None:
        response = httpx.Response(
            200, json={"responses": ["oi"], "tokens_used": 0, "current_agent": None}
        )
        mock_post = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        await send_playground_message(
            tenant_id="t1", contact_phone_number="playground-s1", message="oi"
        )

        body = mock_post.call_args.kwargs["json"]
        assert body["agents"] == []
