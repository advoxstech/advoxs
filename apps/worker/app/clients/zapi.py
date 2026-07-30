"""Cliente HTTP da Z-API usado direto pelo worker — só pelo billing gate
determinístico (apps/worker/app/billing_gate.py), que precisa mandar texto
e listas interativas SEM passar pelo agents service (é esse desvio que
elimina o custo de LLM nesse trecho do funil). Duplicado deliberadamente de
apps/api/app/clients/zapi.py — mesmo padrão já usado no projeto pra evitar
acoplamento entre serviços deployados separadamente (ver
apps/worker/app/clients/whatsapp.py, que já duplica o cliente Meta do api
pelo mesmo motivo)."""

import logging

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.z-api.io"
_TIMEOUT = 15


class ZApiNetworkError(Exception):
    """Falha de rede ao chamar a Z-API (timeout, conexão, DNS)."""


class ZApiApiError(Exception):
    """A Z-API respondeu com erro (credenciais inválidas, instância não encontrada, etc.)."""


def _headers(client_token: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    if client_token:
        headers["Client-Token"] = client_token
    return headers


def _instance_url(instance_id: str, token: str, path: str) -> str:
    return f"{_BASE_URL}/instances/{instance_id}/token/{token}/{path}"


def _zapi_error_message(response: httpx.Response, fallback: str) -> str:
    try:
        body = response.json()
    except ValueError:
        return fallback
    if isinstance(body, dict):
        for key in ("error", "message", "msg"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return fallback


async def send_zapi_text_message(
    instance_id: str, token: str, client_token: str | None, to: str, text: str
) -> None:
    """Envia mensagem de texto pela Z-API — equivalente a
    app.clients.whatsapp.send_text_message (Meta)."""
    url = _instance_url(instance_id, token, "send-text")
    payload = {"phone": to, "message": text}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, headers=_headers(client_token), json=payload)
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao enviar mensagem pela Z-API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Z-API (send-text) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError(
            _zapi_error_message(response, "Não foi possível enviar a mensagem pela Z-API")
        )


async def send_zapi_option_list(
    instance_id: str,
    token: str,
    client_token: str | None,
    to: str,
    message: str,
    title: str,
    button_label: str,
    options: list[dict],
) -> None:
    """Envia uma lista de opções pela Z-API (`send-option-list`) — equivalente
    a app.clients.whatsapp.send_interactive_list_message (Meta), mas sem o
    conceito de seções nomeadas: `options` é uma lista flat, cada item
    `{"id": str, "title": str, "description": str}` (mesmo formato de linha
    já usado pra Meta)."""
    url = _instance_url(instance_id, token, "send-option-list")
    payload = {
        "phone": to,
        "message": message,
        "optionList": {"title": title, "buttonLabel": button_label, "options": options},
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, headers=_headers(client_token), json=payload)
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao enviar lista pela Z-API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Z-API (send-option-list) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError(
            _zapi_error_message(response, "Não foi possível enviar a lista pela Z-API")
        )
