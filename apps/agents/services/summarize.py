"""Resumo de conversa sob demanda — chamada direta ao LLM, sem grafo/tools.

Diferente de `run_agent` (services/call_agent.py), aqui não há checkpoint nem
histórico persistido: o `api` já manda o histórico completo em cada chamada.
"""

from langchain_core.messages import HumanMessage, SystemMessage

from agents.nodes import model
from services.call_agent import langfuse_handler, sum_usage_breakdown

SUMMARY_PROMPT = (
    "Resuma esta conversa entre um cliente e o escritório de advocacia em até "
    "3 frases, em português, focando no problema ou pedido do cliente e no "
    "que já foi resolvido."
)

_SENDER_LABELS = {"contact": "Cliente", "agent": "Atendente", "human": "Atendente"}


def _format_transcript(messages: list[dict]) -> str:
    lines = [
        f"{_SENDER_LABELS.get(m['sender_type'], m['sender_type'])}: {m['content']}"
        for m in messages
    ]
    return "\n".join(lines)


async def summarize_conversation(messages: list[dict]) -> tuple[str, dict]:
    transcript = _format_transcript(messages)
    response = await model.ainvoke(
        [SystemMessage(content=SUMMARY_PROMPT), HumanMessage(content=transcript)],
        config={"callbacks": [langfuse_handler]},
    )
    usage = sum_usage_breakdown([response])
    return response.content, usage
