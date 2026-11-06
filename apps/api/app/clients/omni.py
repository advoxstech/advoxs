"""Cliente da API própria do Z-API Omni (WhatsApp oficial).

O envio de mensagens livres usa a camada de compatibilidade Z-API (ver
app/clients/zapi.py, `omni=True`). Aqui ficam as rotas que só existem na API
própria: webhooks do canal, consulta do canal e modelos de mensagem.

Autenticação: `Authorization: Bearer {OMNI_SECRET_KEY}` (uma chave do
workspace da Advoxs). Referência: https://developer.omni.z-api.io
"""

import gzip
import hashlib
import hmac
import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 20
# Eventos assinados no webhook de cada canal: mensagens recebidas (formato
# Z-API, igual ao da Z-API clássica) e mudanças de conexão.
WEBHOOK_EVENTS = ["MESSAGE_RECEIVED", "CONNECTED", "DISCONNECTED"]
SIGNATURE_TOLERANCE_SECONDS = 300


class OmniNetworkError(Exception):
    """Falha de rede ao chamar o Omni (timeout, conexão, DNS)."""


class OmniApiError(Exception):
    """O Omni respondeu com erro."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.omni_secret_key}",
        "Content-Type": "application/json",
    }


def _url(path: str) -> str:
    return f"{settings.omni_api_base_url.rstrip('/')}{path}"


def _error_message(response: httpx.Response, fallback: str) -> str:
    try:
        body = response.json()
    except ValueError:
        return fallback
    if isinstance(body, dict):
        for key in ("message", "error"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return fallback


async def _request(method: str, path: str, fallback: str, **kwargs) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(method, _url(path), headers=_headers(), **kwargs)
    except httpx.HTTPError as exc:
        raise OmniNetworkError(f"Falha de rede ao chamar o Omni ({path.split('/')[2]})") from exc
    if response.is_error:
        logger.warning(
            "Omni retornou erro | rota=%s status=%s", path.split("?")[0], response.status_code
        )
        raise OmniApiError(_error_message(response, fallback), response.status_code)
    return response


async def create_channel_webhook(channel_id: str, url: str) -> tuple[str, str]:
    """Cria o webhook do canal no formato Z-API, com assinatura HMAC.

    Devolve (id do webhook, segredo HMAC). O segredo só aparece nesta resposta.
    """
    response = await _request(
        "POST",
        f"/v1/channels/{channel_id}/webhooks",
        "Não foi possível criar o webhook no Omni",
        json={
            "url": url,
            "events": WEBHOOK_EVENTS,
            "payloadFormat": "Z_API",
            "signing": True,
            "description": "Advoxs",
        },
    )
    body = response.json()
    webhook_id, secret = body.get("id"), body.get("secret")
    if not webhook_id or not secret:
        raise OmniApiError("O Omni não devolveu o id e o segredo do webhook")
    return str(webhook_id), str(secret)


async def delete_channel_webhook(channel_id: str, webhook_id: str) -> None:
    await _request(
        "DELETE",
        f"/v1/channels/{channel_id}/webhooks/{webhook_id}",
        "Não foi possível remover o webhook no Omni",
    )


async def get_channel(channel_id: str) -> dict | None:
    """Dados do canal (conexão, número, WABA). None quando a conta não tem a
    função ENTERPRISE, que essa rota exige (422)."""
    try:
        response = await _request(
            "GET", f"/v1/channels/{channel_id}", "Não foi possível consultar o canal no Omni"
        )
    except OmniApiError as exc:
        if exc.status_code == 422:
            logger.info("Consulta de canal indisponível no Omni (sem função ENTERPRISE)")
            return None
        raise
    return response.json()


async def list_waba_templates(waba_id: str, limit: int = 500) -> list[dict]:
    """Modelos da conta de WhatsApp Business, no mesmo formato da Graph API."""
    response = await _request(
        "GET",
        f"/whatsapp/businesses/{waba_id}/templates",
        "Não foi possível listar os modelos no Omni",
        params={"limit": limit},
    )
    data = response.json().get("data")
    return data if isinstance(data, list) else []


async def send_template(
    channel_id: str, to: str, name: str, language: str, components: list[dict]
) -> str | None:
    """Envia um modelo aprovado. `components` no formato da Graph API."""
    response = await _request(
        "POST",
        f"/v1/channels/{channel_id}/messages",
        "Não foi possível enviar o modelo pelo Omni",
        json={
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
        },
    )
    return response.json().get("messageId")


def valid_webhook_signature(
    secret: str,
    raw_body: bytes,
    signature_header: str | None,
    idempotency_header: str | None,
    content_encoding: str | None = None,
    now: float | None = None,
) -> bool:
    """Valida o `x-webhook-signature` do Omni.

    O HMAC-SHA256 é calculado sobre `t\\ntopic\\npartition\\noffset\\ncorpo`
    (topic/partition/offset vêm do `x-idempotency-key`), com o corpo
    descomprimido quando chega em gzip. Recusa assinaturas com mais de 5 min.
    """
    if not secret or not signature_header or not idempotency_header:
        return False
    try:
        parts = dict(p.strip().split("=", 1) for p in signature_header.split(","))
        t, v1 = parts["t"], parts["v1"]
        topic, partition, offset = idempotency_header.rsplit(":", 2)
        body = gzip.decompress(raw_body) if content_encoding == "gzip" else raw_body
        timestamp = int(t)
    except (ValueError, KeyError, OSError):
        return False
    current = time.time() if now is None else now
    if abs(current - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    canonical = b"\n".join([t.encode(), topic.encode(), partition.encode(), offset.encode(), body])
    expected = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)
