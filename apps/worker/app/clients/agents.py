import httpx

from app.config import settings


async def send_message_to_agents(
    http: httpx.AsyncClient,
    *,
    tenant_id: str,
    contact_phone_number: str,
    message: str,
    phone_number_id: str,
    access_token: str,
    agents: list[dict] | None = None,
) -> dict | None:
    """Chama POST /messages do agents service.

    Retorna {"responses": [...], "tokens_used": N, "tokens_input": N,
    "tokens_output": N, "delivery_failures": [...]}, ou None quando o agents
    devolve 202 (a mensagem foi agrupada pelo debounce numa execução já em
    andamento — as respostas virão pela execução que está rodando).
    tokens_input/tokens_output valem 0 quando o agents ainda não devolve o
    breakdown (versão antiga durante o deploy).

    `agents`: a lista de agentes do tenant (id, name, instructions,
    is_entry_point, knowledge_base_file_ids) — resolvida aqui a partir do
    Postgres do monorepo antes da chamada; o agents service nunca acessa
    esse banco diretamente.
    """
    headers = {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}
    payload = {
        "tenant_id": tenant_id,
        "contact_phone_number": contact_phone_number,
        "message": message,
        "attachments": [],
        "phone_number_id": phone_number_id,
        "access_token": access_token,
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
        "delivery_failures": data.get("delivery_failures", []),
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
