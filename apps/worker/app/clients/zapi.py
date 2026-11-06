"""Cliente HTTP da Z-API usado direto pelo worker — billing gate determinístico
(apps/worker/app/billing_gate.py), entregas da caixa de saída, follow-up e o
aviso de leitura demorada. Duplicado deliberadamente de
apps/api/app/clients/zapi.py — mesmo padrão já usado no projeto pra evitar
acoplamento entre serviços deployados separadamente (ver
apps/worker/app/clients/whatsapp.py, que já duplica o cliente Meta do api
pelo mesmo motivo).

`omni=True`: envia pela camada de compatibilidade do Z-API Omni (WhatsApp
oficial). Mesmos caminhos e corpos; muda o endereço base (`/channels/{canal}`)
e a autenticação (Secret Key do workspace da Advoxs no lugar do Client-Token).
"""

import logging
from urllib.parse import urlsplit

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.z-api.io"
_TIMEOUT = 15
_DEFAULT_DOCUMENT_EXTENSION = "pdf"
# A API oficial do WhatsApp aceita no máximo 3 botões de resposta rápida.
MAX_BUTTONS = 3


class ZApiNetworkError(Exception):
    """Falha de rede ao chamar a Z-API (timeout, conexão, DNS)."""


class ZApiApiError(Exception):
    """A Z-API respondeu com erro (credenciais inválidas, instância não encontrada, etc.)."""


def _headers(client_token: str | None, omni: bool = False) -> dict:
    headers = {"Content-Type": "application/json"}
    if omni:
        headers["Authorization"] = f"Bearer {settings.omni_secret_key}"
    elif client_token:
        headers["Client-Token"] = client_token
    return headers


def _instance_url(instance_id: str, token: str, path: str, omni: bool = False) -> str:
    if omni:
        base = settings.omni_compat_base_url.rstrip("/")
        return f"{base}/channels/{instance_id}/token/{token}/{path}"
    return f"{_BASE_URL}/instances/{instance_id}/token/{token}/{path}"


def _infer_document_extension(filename: str | None, link: str) -> str:
    source = filename or link
    last_segment = urlsplit(source).path.rsplit("/", 1)[-1]
    parts = last_segment.rsplit(".", 1)
    return parts[1].lower() if len(parts) == 2 and parts[1] else _DEFAULT_DOCUMENT_EXTENSION


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


async def _post(url: str, headers: dict, payload: dict, rota: str, fallback: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao chamar a Z-API ({rota})") from exc
    if response.is_error:
        logger.warning("Z-API (%s) retornou erro | status=%s", rota, response.status_code)
        raise ZApiApiError(_zapi_error_message(response, fallback))


async def send_zapi_text_message(
    instance_id: str,
    token: str,
    client_token: str | None,
    to: str,
    text: str,
    *,
    omni: bool = False,
) -> None:
    """Envia mensagem de texto pela Z-API — equivalente a
    app.clients.whatsapp.send_text_message (Meta)."""
    await _post(
        _instance_url(instance_id, token, "send-text", omni),
        _headers(client_token, omni),
        {"phone": to, "message": text},
        "send-text",
        "Não foi possível enviar a mensagem pela Z-API",
    )


async def send_zapi_document_message(
    instance_id: str,
    token: str,
    client_token: str | None,
    to: str,
    link: str,
    filename: str | None = None,
    *,
    omni: bool = False,
) -> None:
    extension = _infer_document_extension(filename, link)
    payload: dict[str, str] = {"phone": to, "document": link}
    if filename:
        payload["fileName"] = filename
    await _post(
        _instance_url(instance_id, token, f"send-document/{extension}", omni),
        _headers(client_token, omni),
        payload,
        "send-document",
        "Não foi possível enviar o documento pela Z-API",
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
    já usado pra Meta). Não existe no Z-API Omni (ver send_zapi_button_list)."""
    await _post(
        _instance_url(instance_id, token, "send-option-list"),
        _headers(client_token),
        {
            "phone": to,
            "message": message,
            "optionList": {"title": title, "buttonLabel": button_label, "options": options},
        },
        "send-option-list",
        "Não foi possível enviar a lista pela Z-API",
    )


async def send_zapi_button_list(
    instance_id: str,
    token: str,
    client_token: str | None,
    to: str,
    message: str,
    buttons: list[dict],
    *,
    omni: bool = False,
) -> None:
    """Envia texto com botões de resposta rápida (`send-button-list`), até 3.

    `buttons`: `[{"id": str, "label": str}]`. A escolha volta no webhook como
    `buttonsResponseMessage.message` (o rótulo do botão)."""
    await _post(
        _instance_url(instance_id, token, "send-button-list", omni),
        _headers(client_token, omni),
        {"phone": to, "message": message, "buttonList": {"buttons": buttons[:MAX_BUTTONS]}},
        "send-button-list",
        "Não foi possível enviar os botões pela Z-API",
    )
