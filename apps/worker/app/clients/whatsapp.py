"""Envio de mensagem via WhatsApp Cloud API (Graph API) direto do worker —
usado só pelo billing gate determinístico (apps/worker/app/billing_gate.py),
que precisa mandar texto e listas interativas SEM passar pelo agents
service (é esse desvio que elimina o custo de LLM nesse trecho do funil).
Duplicado deliberadamente de apps/api/app/clients/whatsapp.py — mesmo padrão
já usado no projeto pra evitar acoplamento entre serviços deployados
separadamente (ex: calcular_creditos)."""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class WhatsAppSendError(Exception):
    pass


def _url(phone_number_id: str) -> str:
    return f"{settings.graph_api_base_url}/{settings.graph_api_version}/{phone_number_id}/messages"


async def _post(phone_number_id: str, access_token: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                _url(phone_number_id),
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise WhatsAppSendError(f"Falha de rede ao chamar a Graph API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Graph API retornou erro | status=%s body=%s", response.status_code, response.text
        )
        raise WhatsAppSendError(f"Graph API HTTP {response.status_code}: {response.text}")


async def send_text_message(phone_number_id: str, access_token: str, to: str, text: str) -> None:
    await _post(
        phone_number_id,
        access_token,
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text},
        },
    )


async def send_interactive_list_message(
    phone_number_id: str,
    access_token: str,
    to: str,
    header: str,
    body: str,
    sections: list[dict],
    button_text: str = "Ver opções",
) -> None:
    """`sections`: `[{"title": str, "rows": [{"id": str, "title": str, "description": str}]}]`.
    Limite da Meta: até 10 seções, no máximo 10 linhas somadas entre todas."""
    await _post(
        phone_number_id,
        access_token,
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": header},
                "body": {"text": body},
                "action": {"button": button_text, "sections": sections},
            },
        },
    )
