from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

import services.summarize as summarize_module


@pytest.fixture
def mock_model(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(summarize_module, "model", mock)
    return mock


class TestSummarizeConversation:
    async def test_gera_resumo_e_soma_os_tokens(self, mock_model) -> None:
        mock_model.ainvoke.return_value = AIMessage(
            content="Cliente perguntou sobre condomínio e o especialista respondeu.",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

        summary, tokens_used = await summarize_module.summarize_conversation(
            [
                {"sender_type": "contact", "content": "Oi, preciso de ajuda com o condomínio"},
                {"sender_type": "agent", "content": "Claro, qual é a dúvida?"},
            ]
        )

        assert summary == "Cliente perguntou sobre condomínio e o especialista respondeu."
        assert tokens_used == 15
        mock_model.ainvoke.assert_awaited_once()

    async def test_monta_a_transcricao_com_rotulos_em_portugues(self, mock_model) -> None:
        mock_model.ainvoke.return_value = AIMessage(content="resumo", usage_metadata=None)

        await summarize_module.summarize_conversation(
            [
                {"sender_type": "contact", "content": "Pergunta do cliente"},
                {"sender_type": "human", "content": "Resposta do atendente humano"},
            ]
        )

        transcript = mock_model.ainvoke.call_args.args[0][1].content
        assert "Cliente: Pergunta do cliente" in transcript
        assert "Atendente: Resposta do atendente humano" in transcript

    async def test_sem_usage_metadata_retorna_zero_tokens(self, mock_model) -> None:
        mock_model.ainvoke.return_value = AIMessage(content="resumo", usage_metadata=None)

        _, tokens_used = await summarize_module.summarize_conversation(
            [{"sender_type": "contact", "content": "oi"}]
        )

        assert tokens_used == 0
