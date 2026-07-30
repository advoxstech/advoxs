"""Cliente HTTP da Z-API — provedor não-oficial de WhatsApp (conexão por QR
code, sem aprovação de negócio). Mesmo padrão de exceções de
app/clients/whatsapp.py (Meta): ZApiNetworkError pra falha de rede,
ZApiApiError pra erro retornado pela própria Z-API.

Decisão de formato de chamada (Task 2, 2026-07-29): a documentação oficial da
Z-API (developer.z-api.io) é inconsistente/incompleta entre endpoints sobre
onde `instanceId`/`token` devem ir — alguns exemplos (ex: send-text, já usado
em pesquisa anterior deste projeto) mostram os dois no path da URL; as
páginas de status/qr-code-image/device/webhooks/disconnect consultadas nesta
task (WebFetch, 2026-07-29) não expõem a URL completa do endpoint, só listam
`instanceId`/`token` genericamente como "headers" sem confirmar o path real
nem contradizer o padrão de path já observado. Diante da ambiguidade,
adotamos `instanceId`/`token` no PATH da URL para todos os endpoints deste
arquivo — consistente com send-text, mais fácil de testar (sem precisar
inspecionar headers da request) e values já validados em produção por outros
consumidores da Z-API. `Client-Token` (opcional, só quando a conta usa Client
por conta na Z-API) continua sendo um header, nunca faz parte do path.
"""

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
    """Extrai a mensagem de erro do corpo da resposta da Z-API, quando
    presente — mesmo espírito de `_meta_error_message` em
    app/clients/whatsapp.py. A doc da Z-API não documenta um schema de erro
    único e consistente entre endpoints, então tenta as chaves mais comuns
    observadas (`error`, `message`, `msg`) antes de cair no fallback
    genérico."""
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


async def check_zapi_status(instance_id: str, token: str, client_token: str | None) -> dict:
    """Valida as credenciais e devolve o status de pareamento — usado tanto
    na validação inicial (conecta mesmo sem estar pareado ainda) quanto no
    polling de conexão (ver GET /whatsapp/zapi-status)."""
    url = _instance_url(instance_id, token, "status")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, headers=_headers(client_token))
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao consultar status na Z-API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Z-API (status) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError(
            _zapi_error_message(response, "Não foi possível validar as credenciais com a Z-API")
        )
    return response.json()


async def configure_zapi_webhook(
    instance_id: str, token: str, client_token: str | None, webhook_url: str
) -> None:
    """Configura a URL do webhook de mensagem recebida via API — a Z-API
    entrega isso automaticamente, sem o tenant precisar colar nada num painel.

    Endpoint correto confirmado contra a doc oficial (developer.z-api.io,
    webhooks/on-message-received) depois de descobrir em produção que o
    endpoint usado antes (`POST .../webhooks`) não existe: `PUT
    .../update-webhook-received` (sem o sufixo `-delivery`, que também
    notificaria mensagens enviadas pelo próprio número — não é o que
    queremos aqui, o mesmo propósito do antigo `notifySentByMe=False`)."""
    url = _instance_url(instance_id, token, "update-webhook-received")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.put(
                url, headers=_headers(client_token), json={"value": webhook_url}
            )
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao configurar webhook na Z-API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Z-API (update-webhook-received) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError(
            _zapi_error_message(response, "Não foi possível configurar o webhook na Z-API")
        )

    body = response.json()
    if isinstance(body, dict) and body.get("error"):
        # A Z-API pode responder 200 com um erro só no corpo (rota errada,
        # payload rejeitado) — nunca confiar só no status HTTP aqui. É
        # exatamente esse comportamento que mascarou o bug do endpoint
        # antigo por toda a vida desta feature até agora.
        logger.warning(
            "Z-API (update-webhook-received) respondeu 200 com erro no corpo | body=%s",
            response.text,
        )
        raise ZApiApiError(
            _zapi_error_message(response, "Não foi possível configurar o webhook na Z-API")
        )


async def fetch_zapi_qrcode(instance_id: str, token: str, client_token: str | None) -> str:
    """Devolve a imagem do QR code (base64, já pronta pra exibir num <img src=...>)."""
    url = _instance_url(instance_id, token, "qr-code-image")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, headers=_headers(client_token))
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao buscar QR code na Z-API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Z-API (qr-code-image) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError(
            _zapi_error_message(response, "Não foi possível obter o QR code da Z-API")
        )
    body = response.json()
    if "value" not in body:
        # A Z-API responde 200 sem `value` quando a instância já está
        # conectada (não há QR pra mostrar) — visto em produção com o corpo
        # `{"error": "NOT_FOUND", "message": "Unable to find matching target
        # resource method"}`. `connect_zapi` já deveria ter detectado esse
        # caso antes de chegar aqui (ver check_zapi_status), mas nunca confiar
        # só nisso — sem essa checagem, isso vira um KeyError não tratado.
        logger.warning(
            "Z-API (qr-code-image) resposta sem QR code | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError(
            _zapi_error_message(
                response, "A Z-API não retornou um QR code — a instância já pode estar conectada"
            )
        )
    return body["value"]


async def fetch_zapi_connected_phone(
    instance_id: str, token: str, client_token: str | None
) -> str | None:
    """Número conectado à instância, só disponível depois do QR pareado —
    None se a Z-API ainda não devolver o campo (pareamento incompleto)."""
    url = _instance_url(instance_id, token, "device")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, headers=_headers(client_token))
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao buscar dispositivo na Z-API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Z-API (device) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError(
            _zapi_error_message(
                response, "Não foi possível consultar o dispositivo conectado na Z-API"
            )
        )
    return response.json().get("phone")


async def disconnect_zapi_instance(instance_id: str, token: str, client_token: str | None) -> None:
    """Desconecta (desparea) a instância na Z-API — chamado best-effort pelo
    POST /whatsapp/disconnect local antes de marcar status="disconnected"
    no banco; uma falha aqui nunca impede a desconexão local."""
    url = _instance_url(instance_id, token, "disconnect")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, headers=_headers(client_token))
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao desconectar na Z-API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Z-API (disconnect) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError(
            _zapi_error_message(response, "Não foi possível desconectar a instância na Z-API")
        )


async def send_zapi_text_message(
    instance_id: str, token: str, client_token: str | None, to: str, text: str
) -> None:
    """Envia mensagem de texto pela Z-API — equivalente a
    app.clients.whatsapp.send_text_message (Meta), usado pelo roteamento por
    provedor em app/services/whatsapp_outbound.py (takeover humano, aviso de
    isenção de cobrança, confirmação de compra do cliente final)."""
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
