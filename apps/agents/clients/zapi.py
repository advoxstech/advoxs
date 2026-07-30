"""Cliente da Z-API (provedor não-oficial de WhatsApp, conexão por QR
code). Mesma interface de clients/whatsapp.py (Meta) — quem chama
(api/routes.py) não precisa saber qual dos dois está por trás. Retry/rate
limit duplicados deliberadamente de WhatsAppClient — ver docstring da
Task 8 do plano de implementação (mesmo princípio de isolamento já
documentado no projeto pra clientes de canal)."""

import asyncio
import time
from urllib.parse import urlsplit

import httpx
from loguru import logger

from clients.ratelimit import acquire_rate_limit_slot

_BASE_URL = "https://api.z-api.io"
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = [0.5, 1]
_DEFAULT_DOCUMENT_EXTENSION = "pdf"


def _infer_extension(filename: str | None, link: str) -> str:
    """A Z-API exige a extensão do arquivo como segmento de path do endpoint
    de documento (`.../send-document/{extension}`, ex: pdf/docx/xlsx —
    confirmado em developer.z-api.io/message/send-message-document, não é
    um literal fixo "pdf" como um esboço anterior desta task assumia).
    Deriva do `filename` (prioridade) ou do próprio link; sem extensão
    identificável, cai em "pdf" (caso mais comum de documento gerado).

    Usa `urlsplit(source).path` (não uma string inteira) pra isolar só o
    path de fato — evita capturar por engano um "." do domínio (ex:
    "exemplo.com") ou da query string. Um link sem nenhum path após o
    domínio (ex: "https://exemplo.com") tem `.path == ""`, cai direto no
    fallback. `urlsplit` também funciona para `filename` puro (sem
    protocolo/domínio) — o texto inteiro vira o `.path`, comportamento
    idêntico ao de antes pra esse caso."""
    source = filename or link
    if source:
        last_segment = urlsplit(source).path.rsplit("/", 1)[-1]
        candidate = last_segment.rsplit(".", 1)
        if len(candidate) == 2 and candidate[1]:
            return candidate[1].lower()
    return _DEFAULT_DOCUMENT_EXTENSION


class ZApiClient:
    """Cliente da Z-API. Credenciais (instance_id + token, e opcionalmente
    client_token) são por tenant e chegam em cada request — este serviço não
    armazena nem resolve credenciais."""

    def __init__(self, instance_id: str, token: str, client_token: str | None = None):
        self._instance_id = instance_id
        self._token = token
        self._client_token = client_token
        self._base_url = f"{_BASE_URL}/instances/{instance_id}/token/{token}"
        # Versão redigida do base_url pra uso EXCLUSIVO em log — diferente da
        # Meta (token só no header Authorization, nunca logado), a Z-API leva
        # o token da instância no próprio path da URL. Toda chamada de
        # logger.info/warning/error dentro de _safe_request usa isto (via
        # _redact_url) no lugar da url real, que nunca deve aparecer em texto
        # plano em nenhum log — a url real segue sendo usada pra fazer a
        # requisição de verdade.
        self._log_base_url = f"{_BASE_URL}/instances/{instance_id}/token/***"
        self._client: httpx.AsyncClient | None = None
        logger.info("ZApiClient inicializado | instance_id={}", instance_id)

    def _redact_url(self, url: str) -> str:
        """Substitui o prefixo com o token real pelo prefixo redigido,
        preservando o segmento de endpoint (ex: /send-text) — útil pra saber
        qual chamada gerou o log sem vazar a credencial."""
        return url.replace(self._base_url, self._log_base_url)

    # ---------- SESSION LIFECYCLE ----------
    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=15)
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15)
        return self._client

    # ---------- HEADERS ----------
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._client_token:
            headers["Client-Token"] = self._client_token
        return headers

    # ---------- CORE SAFE REQUEST ----------
    async def _safe_request(self, method: str, url: str, **kwargs):
        client = self._get_client()
        last_error: dict = {"success": False, "data": None, "error": "Erro desconhecido"}
        # `url` real (com o token da instância no path) só é usada pra fazer
        # a requisição de verdade — todo log dentro desta função usa a versão
        # redigida, pra nunca vazar a credencial em texto plano.
        log_url = self._redact_url(url)

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            acquired = await acquire_rate_limit_slot(self._instance_id)
            if not acquired:
                last_error = {
                    "success": False,
                    "data": None,
                    "error": "Rate limit excedido — sem vaga liberada a tempo",
                }
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                return last_error

            started_at = time.perf_counter()
            try:
                logger.info(
                    "Executando requisição à Z-API | method={} url={} tentativa={}",
                    method, log_url, attempt,
                )
                response = await client.request(method, url, **kwargs)

                if response.is_error:
                    logger.warning(
                        "Resposta HTTP não OK | method={} url={} status={} body={}",
                        method, log_url, response.status_code, response.text,
                    )
                    last_error = {
                        "success": False,
                        "data": None,
                        "error": f"HTTP {response.status_code}: {response.text}",
                    }
                    if response.status_code < 500:
                        # 4xx não é transitório — falha imediata, sem retry.
                        return last_error
                    if attempt < _MAX_ATTEMPTS:
                        await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                        continue
                    return last_error

                try:
                    data = response.json()
                except Exception:
                    data = response.text

                elapsed = round(time.perf_counter() - started_at, 3)
                logger.info(
                    "Requisição concluída | method={} url={} status={} elapsed={}s",
                    method, log_url, response.status_code, elapsed,
                )
                return {"success": True, "data": data, "error": None}

            except httpx.TimeoutException:
                logger.error(
                    "Timeout ao acessar Z-API | method={} url={} tentativa={}",
                    method, log_url, attempt,
                )
                last_error = {
                    "success": False,
                    "data": None,
                    "error": "Timeout ao acessar Z-API",
                }
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                return last_error

            except httpx.ConnectError as e:
                logger.error(
                    "Erro de conexão com Z-API | method={} url={} error={} tentativa={}",
                    method, log_url, e, attempt,
                )
                last_error = {"success": False, "data": None, "error": f"Erro de conexão: {e}"}
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                return last_error

            except httpx.RequestError as e:
                logger.error(
                    "Erro de requisição à Z-API | method={} url={} error={}", method, log_url, e
                )
                return {"success": False, "data": None, "error": f"Erro de requisição: {e}"}

            except Exception as e:
                logger.exception(
                    "Erro inesperado ao acessar Z-API | method={} url={}", method, log_url
                )
                return {"success": False, "data": None, "error": f"Erro inesperado: {e}"}

        return last_error

    # ---------- MESSAGES ----------
    async def send_text_message(self, to: str, text: str):
        url = f"{self._base_url}/send-text"
        logger.info("Enviando mensagem de texto via Z-API | to={}", to)
        payload = {"phone": to, "message": text}
        return await self._safe_request("POST", url, headers=self._headers(), json=payload)

    async def send_document_message(
        self, to: str, link: str, filename: str | None = None, caption: str | None = None
    ):
        # Confirmado em developer.z-api.io/message/send-message-document: o
        # endpoint leva a extensão do arquivo como segmento de path
        # (`.../send-document/{extension}`, ex: pdf/docx/xlsx) — nunca um
        # literal fixo "pdf". Corpo: {"phone", "document", "fileName"?,
        # "caption"?} (nomes confirmados na doc).
        extension = _infer_extension(filename, link)
        url = f"{self._base_url}/send-document/{extension}"
        logger.info(
            "Enviando documento via Z-API | to={} link={} extension={}", to, link, extension
        )
        payload: dict = {"phone": to, "document": link}
        if filename:
            payload["fileName"] = filename
        if caption:
            payload["caption"] = caption
        return await self._safe_request("POST", url, headers=self._headers(), json=payload)
