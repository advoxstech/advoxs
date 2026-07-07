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
) -> dict | None:
    """Chama POST /messages do agents service.

    Retorna {"responses": [...], "tokens_used": N}, ou None quando o agents
    devolve 202 (a mensagem foi agrupada pelo debounce numa execução já em
    andamento — as respostas virão pela execução que está rodando).
    """
    headers = {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}
    response = await http.post(
        "/messages",
        json={
            "tenant_id": tenant_id,
            "contact_phone_number": contact_phone_number,
            "message": message,
            "attachments": [],
            "phone_number_id": phone_number_id,
            "access_token": access_token,
        },
        headers=headers,
    )
    if response.status_code == 202:
        return None
    response.raise_for_status()
    data = response.json()
    return {
        "responses": data.get("responses", []),
        "tokens_used": data.get("tokens_used", 0),
    }
