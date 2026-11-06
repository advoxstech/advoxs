"""Envio de modelo aprovado pela API própria do Z-API Omni (WhatsApp oficial).

Usado pelo follow-up manual (app/tasks/follow_ups.py). Mensagens livres seguem
pela camada de compatibilidade Z-API (app/clients/zapi.py, `omni=True`).
Duplicado de propósito do cliente do api, como os demais clientes do worker.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 20


class OmniTemplateError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "O Omni recusou o envio do modelo."
    if isinstance(body, dict):
        for key in ("message", "error"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value[:500]
    return "O Omni recusou o envio do modelo."


async def send_template(
    channel_id: str, to: str, name: str, language: str, components: list[dict]
) -> str | None:
    """Envia o modelo e devolve o id da mensagem. Falhas de rede, 429 e 5xx
    são temporárias (o follow-up tenta de novo); as demais são definitivas."""
    url = f"{settings.omni_api_base_url.rstrip('/')}/v1/channels/{channel_id}/messages"
    payload = {
        "recipient": {"identifier": to},
        "content": {
            "type": "TEMPLATE",
            "attachments": [
                {
                    "template": {
                        "name": name,
                        "language": {"policy": "deterministic", "code": language},
                        "components": components,
                    }
                }
            ],
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.omni_secret_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise OmniTemplateError("Falha de rede ao chamar o Omni.", retryable=True) from exc
    if response.is_error:
        logger.warning("Omni (envio de modelo) retornou erro | status=%s", response.status_code)
        retryable = response.status_code == 429 or response.status_code >= 500
        raise OmniTemplateError(_message(response), retryable=retryable)
    return response.json().get("messageId")
