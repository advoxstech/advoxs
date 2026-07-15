"""Envio de mensagem e conexão de número pela WhatsApp Cloud API (Graph API da Meta).

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


class WhatsAppNetworkError(Exception):
    """Falha de rede ao chamar a Graph API (timeout, conexão, DNS)."""


class WhatsAppApiError(Exception):
    """Graph API respondeu com erro (token inválido, PIN incorreto, etc.)."""


def _meta_error_message(response: httpx.Response, fallback: str) -> str:
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return fallback


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


async def fetch_display_phone_number(phone_number_id: str, access_token: str) -> str:
    """Valida o token/phone_number_id contra a Meta e retorna o número formatado."""
    url = f"{settings.graph_api_base_url}/{settings.graph_api_version}/{phone_number_id}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                params={"fields": "display_phone_number"},
            )
    except httpx.HTTPError as exc:
        raise WhatsAppNetworkError(f"Falha de rede ao validar número: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Graph API (GET número) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise WhatsAppApiError(
            _meta_error_message(
                response, "Não foi possível validar o Phone Number ID/token com a Meta"
            )
        )
    return response.json()["display_phone_number"]


async def register_number(phone_number_id: str, access_token: str, pin: str) -> None:
    """Registra o número na Cloud API usando o PIN de 2 fatores do WhatsApp."""
    url = f"{settings.graph_api_base_url}/{settings.graph_api_version}/{phone_number_id}/register"
    payload = {"messaging_product": "whatsapp", "pin": pin}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise WhatsAppNetworkError(f"Falha de rede ao registrar número: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Graph API (register) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise WhatsAppApiError(
            _meta_error_message(
                response, "Não foi possível registrar o número na Meta — verifique o PIN"
            )
        )


async def subscribe_app_to_waba(waba_id: str, access_token: str) -> None:
    """Inscreve o app do tenant na WABA — sem isso a Meta não entrega webhook de mensagem."""
    url = f"{settings.graph_api_base_url}/{settings.graph_api_version}/{waba_id}/subscribed_apps"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError as exc:
        raise WhatsAppNetworkError(f"Falha de rede ao inscrever app na WABA: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Graph API (subscribed_apps) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise WhatsAppApiError(
            _meta_error_message(
                response,
                "Não foi possível inscrever o app na WhatsApp Business Account — confira o WABA ID",
            )
        )
