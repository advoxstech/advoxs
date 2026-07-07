"""Envio de mensagem pela WhatsApp Cloud API (Graph API da Meta).

Usado no takeover humano do painel de conversas — o envio do agente é feito
pelo próprio agents service. Credenciais por tenant, descriptografadas de
whatsapp_numbers na hora do envio.
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class WhatsAppSendError(Exception):
    pass


async def send_text_message(phone_number_id: str, access_token: str, to: str, text: str) -> None:
    url = f"{settings.graph_api_base_url}/{settings.graph_api_version}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                url,
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
