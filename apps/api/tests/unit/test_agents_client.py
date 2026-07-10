from unittest.mock import AsyncMock

import httpx
import pytest

from app.clients.agents import (
    AgentsApiError,
    AgentsNetworkError,
    generate_conversation_summary,
)


class TestGenerateConversationSummary:
    async def test_retorna_resumo_e_tokens(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"summary": "Resumo da conversa.", "tokens_used": 88})
        mock_post = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        result = await generate_conversation_summary([{"sender_type": "contact", "content": "oi"}])

        assert result == {"summary": "Resumo da conversa.", "tokens_used": 88}
        mock_post.assert_awaited_once()
        assert mock_post.call_args.args[0] == "/summaries"
        assert mock_post.call_args.kwargs["json"] == {
            "messages": [{"sender_type": "contact", "content": "oi"}]
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
