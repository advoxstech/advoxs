import httpx

from app.config import settings


async def send_message_to_agents(
    http: httpx.AsyncClient,
    *,
    tenant_id: str,
    contact_phone_number: str,
    message: str,
    agents: list[dict] | None = None,
) -> dict | None:
    """Chama POST /messages do agents service.

    Retorna {"responses": [...], "tokens_used": N, "tokens_input": N,
    "tokens_output": N, "current_agent_id": str | None, "delivery_failures":
    [...], "documents": [...]}, ou None quando o agents devolve 202 (a
    mensagem foi agrupada pelo debounce numa execução já em andamento — as
    respostas virão pela execução que está rodando). `current_agent_id` é o
    agente do tenant que respondeu por último nesta execução — persistido em
    `conversations.current_agent_id` pelo chamador.
    tokens_input/tokens_output valem 0 quando o agents ainda não devolve o
    breakdown (versão antiga durante o deploy).

    `documents`: documentos gerados nesta execução (fazer_contrato/fazer_multa/
    etc, ver apps/agents/agents/tools.py) — cada item tem {"link", "filename"
    e "credit_cost"}. O custo da geração é registrado antes da entrega.

    `agents`: a lista de agentes do tenant (id, name, instructions,
    is_entry_point, knowledge_base_file_ids) — resolvida aqui a partir do
    Postgres do monorepo antes da chamada; o agents service nunca acessa
    esse banco diretamente.

    A geração não envia ao WhatsApp. O worker grava a resposta e uma entrega
    pendente antes de chamar o provedor, para que uma nova tentativa de envio
    não execute a IA novamente.
    """
    headers = {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}
    payload = {
        "tenant_id": tenant_id,
        "contact_phone_number": contact_phone_number,
        "message": message,
        "attachments": [],
        "send_to_whatsapp": False,
        "agents": agents or [],
    }

    response = await http.post("/messages", json=payload, headers=headers)
    if response.status_code == 202:
        return None
    response.raise_for_status()
    data = response.json()
    return {
        "responses": data.get("responses", []),
        "tokens_used": data.get("tokens_used", 0),
        "tokens_input": data.get("tokens_input", 0),
        "tokens_output": data.get("tokens_output", 0),
        "current_agent_id": data.get("current_agent_id"),
        "delivery_failures": data.get("delivery_failures", []),
        "documents": data.get("documents", []),
    }


async def sync_context_to_agents(
    http: httpx.AsyncClient,
    *,
    tenant_id: str,
    contact_phone_number: str,
    role: str,
    content: str,
) -> None:
    """POST /conversations/{thread_id}/context — anexa mensagem do takeover ao
    checkpoint do LangGraph (sem LLM, sem débito de créditos)."""
    headers = {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}
    thread_id = f"{tenant_id}:{contact_phone_number}"
    response = await http.post(
        f"/conversations/{thread_id}/context",
        json={"messages": [{"role": role, "content": content}]},
        headers=headers,
    )
    response.raise_for_status()


async def replace_context_to_agents(
    http: httpx.AsyncClient,
    *,
    tenant_id: str,
    contact_phone_number: str,
    messages: list[dict[str, str]],
) -> None:
    """Substitui a memória do agente pelo histórico persistido no monorepo."""
    headers = {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}
    thread_id = f"{tenant_id}:{contact_phone_number}"
    response = await http.put(
        f"/conversations/{thread_id}/context",
        json={"messages": messages},
        headers=headers,
    )
    response.raise_for_status()
