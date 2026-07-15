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
    end_customer_billing: dict | None = None,
) -> dict | None:
    """Chama POST /messages do agents service.

    Retorna {"responses": [...], "tokens_used": N, "delivery_failures": [...]},
    ou None quando o agents devolve 202 (a mensagem foi agrupada pelo debounce
    numa execução já em andamento — as respostas virão pela execução que está
    rodando).

    `end_customer_billing` (quando não None) leva {"enabled", "balance",
    "packages"} do cliente final — nenhum dado sensível, a secret key da
    Stripe do tenant nunca sai do api.
    """
    headers = {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}
    payload = {
        "tenant_id": tenant_id,
        "contact_phone_number": contact_phone_number,
        "message": message,
        "attachments": [],
        "phone_number_id": phone_number_id,
        "access_token": access_token,
    }
    if end_customer_billing is not None:
        payload["end_customer_billing"] = end_customer_billing

    response = await http.post("/messages", json=payload, headers=headers)
    if response.status_code == 202:
        return None
    response.raise_for_status()
    data = response.json()
    return {
        "responses": data.get("responses", []),
        "tokens_used": data.get("tokens_used", 0),
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
