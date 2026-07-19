"""Client HTTP para o agents service — usado hoje só pelo playground do
admin (mensagens reais de WhatsApp são enviadas pelo `worker`, não pelo `api`)."""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 120
_DELETE_TIMEOUT_SECONDS = 15


class AgentsNetworkError(Exception):
    """Falha de rede ao chamar o agents service (timeout, conexão, DNS)."""


class AgentsApiError(Exception):
    """O agents service respondeu com erro (não-2xx, exceto 202)."""


def _auth_headers() -> dict[str, str]:
    return {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}


async def send_playground_message(
    *, tenant_id: str, contact_phone_number: str, message: str
) -> dict | None:
    """POST /messages no agents, sem enviar pelo WhatsApp (send_to_whatsapp=False).

    Retorna {"responses": [...], "tokens_used": N, "tokens_input": N,
    "tokens_output": N, "current_agent": "..."}, ou None quando o agents
    devolve 202 (debounce agrupou a mensagem numa execução em andamento —
    as respostas virão pela execução que já roda).
    """
    payload = {
        "tenant_id": tenant_id,
        "contact_phone_number": contact_phone_number,
        "message": message,
        "attachments": [],
        "phone_number_id": "",
        "access_token": "",
        "send_to_whatsapp": False,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.agents_service_url, timeout=_TIMEOUT_SECONDS
        ) as client:
            response = await client.post("/messages", json=payload, headers=_auth_headers())
    except httpx.HTTPError as exc:
        raise AgentsNetworkError(f"Falha de rede ao chamar o agents: {exc}") from exc

    if response.status_code == 202:
        return None
    if response.is_error:
        logger.warning(
            "agents retornou erro no playground | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise AgentsApiError(f"agents HTTP {response.status_code}")

    data = response.json()
    return {
        "responses": data.get("responses", []),
        "tokens_used": data.get("tokens_used"),
        "tokens_input": data.get("tokens_input", 0),
        "tokens_output": data.get("tokens_output", 0),
        "current_agent": data.get("current_agent"),
    }


async def delete_agent_checkpoint(thread_id: str) -> None:
    """DELETE /conversations/{thread_id} no agents — limpa o checkpoint do
    LangGraph. Melhor esforço: loga e segue em caso de falha, nunca bloqueia
    o chamador (usado tanto pelo playground de admin quanto pela exclusão de
    conversas reais/de teste do painel do tenant)."""
    try:
        async with httpx.AsyncClient(
            base_url=settings.agents_service_url, timeout=_DELETE_TIMEOUT_SECONDS
        ) as client:
            await client.delete(f"/conversations/{thread_id}", headers=_auth_headers())
    except httpx.HTTPError as exc:
        logger.warning(
            "Falha ao apagar checkpoint do agente | thread_id=%s erro=%s", thread_id, exc
        )


async def generate_conversation_summary(messages: list[dict]) -> dict:
    """POST /summaries no agents — resumo sob demanda de uma conversa completa.

    Retorna {"summary": str, "tokens_used": int, "tokens_input": int,
    "tokens_output": int}.
    """
    payload = {"messages": messages}
    try:
        async with httpx.AsyncClient(
            base_url=settings.agents_service_url, timeout=_TIMEOUT_SECONDS
        ) as client:
            response = await client.post("/summaries", json=payload, headers=_auth_headers())
    except httpx.HTTPError as exc:
        raise AgentsNetworkError(f"Falha de rede ao chamar o agents: {exc}") from exc

    if response.is_error:
        logger.warning(
            "agents retornou erro ao gerar resumo | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise AgentsApiError(f"agents HTTP {response.status_code}")

    data = response.json()
    if "summary" not in data:
        raise AgentsApiError("agents retornou resposta sem 'summary'")
    return {
        "summary": data["summary"],
        "tokens_used": data.get("tokens_used", 0),
        "tokens_input": data.get("tokens_input", 0),
        "tokens_output": data.get("tokens_output", 0),
    }


_CONTEXT_TIMEOUT_SECONDS = 15


async def sync_conversation_context(
    *, tenant_id: str, contact_phone_number: str, role: str, content: str
) -> None:
    """POST /conversations/{thread_id}/context — anexa uma mensagem do takeover
    ao checkpoint do LangGraph (sem LLM, sem débito). Levanta AgentsNetworkError/
    AgentsApiError; o call site decide se é best-effort."""
    thread_id = f"{tenant_id}:{contact_phone_number}"
    payload = {"messages": [{"role": role, "content": content}]}
    try:
        async with httpx.AsyncClient(
            base_url=settings.agents_service_url, timeout=_CONTEXT_TIMEOUT_SECONDS
        ) as client:
            response = await client.post(
                f"/conversations/{thread_id}/context", json=payload, headers=_auth_headers()
            )
    except httpx.HTTPError as exc:
        raise AgentsNetworkError(f"Falha de rede ao sincronizar contexto: {exc}") from exc

    if response.is_error:
        logger.warning(
            "agents retornou erro no sync de contexto | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise AgentsApiError(f"agents retornou {response.status_code} no sync de contexto")
