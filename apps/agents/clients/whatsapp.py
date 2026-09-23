import asyncio
import os
import time

import httpx
from dotenv import load_dotenv
from loguru import logger

from clients.ratelimit import acquire_rate_limit_slot
from core.safe_logging import safe_error, safe_identifier, safe_url

load_dotenv()

GRAPH_API_BASE_URL = os.getenv("GRAPH_API_BASE_URL", "https://graph.facebook.com")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v23.0")

# Retry curto só para falha transitória (timeout/conexão/5xx) — 4xx nunca é
# retried (não é transitório, retry só desperdiça tempo).
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = [0.5, 1]


class WhatsAppClient:
    """Cliente da WhatsApp Cloud API (Graph API da Meta).

    As credenciais (phone_number_id + access_token) são por tenant e chegam
    em cada request — este serviço não armazena nem resolve credenciais.
    """

    def __init__(self, phone_number_id: str, access_token: str):
        self._phone_number_id = phone_number_id
        self._access_token = access_token
        self._base_url = f"{GRAPH_API_BASE_URL}/{GRAPH_API_VERSION}"
        self._client: httpx.AsyncClient | None = None
        logger.info(
            "WhatsAppClient inicializado | provider_ref={}", safe_identifier(phone_number_id)
        )

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

    # ---------- CORE SAFE REQUEST ----------
    async def _safe_request(self, method: str, url: str, **kwargs):
        client = self._get_client()
        last_error: dict = {"success": False, "data": None, "error": "Erro desconhecido"}

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            acquired = await acquire_rate_limit_slot(self._phone_number_id)
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
                    "Executando requisição à Graph API | method={} url={} tentativa={}",
                    method,
                    safe_url(url),
                    attempt,
                )
                response = await client.request(method, url, **kwargs)

                if response.is_error:
                    logger.warning(
                        "Resposta HTTP não OK | method={} url={} status={}",
                        method,
                        safe_url(url),
                        response.status_code,
                    )
                    last_error = {
                        "success": False,
                        "data": None,
                        "error": f"HTTP {response.status_code}",
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
                    method,
                    safe_url(url),
                    response.status_code,
                    elapsed,
                )
                return {"success": True, "data": data, "error": None}

            except httpx.TimeoutException:
                logger.error(
                    "Timeout ao acessar Graph API | method={} url={} tentativa={}",
                    method,
                    safe_url(url),
                    attempt,
                )
                last_error = {
                    "success": False,
                    "data": None,
                    "error": "Timeout ao acessar Graph API",
                }
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                return last_error

            except httpx.ConnectError as exc:
                logger.error(
                    "Erro de conexão com Graph API | method={} url={} error={} tentativa={}",
                    method,
                    safe_url(url),
                    safe_error(exc),
                    attempt,
                )
                last_error = {"success": False, "data": None, "error": "Erro de conexão"}
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                return last_error

            except httpx.RequestError as exc:
                logger.error(
                    "Erro de requisição à Graph API | method={} url={} error_type={}",
                    method,
                    safe_url(url),
                    safe_error(exc),
                )
                return {"success": False, "data": None, "error": "Erro de requisição"}

            except Exception as exc:
                logger.error(
                    "Erro inesperado ao acessar Graph API | method={} url={} error_type={}",
                    method,
                    safe_url(url),
                    safe_error(exc),
                )
                return {"success": False, "data": None, "error": "Erro inesperado"}

        return last_error

    # ---------- HEADERS ----------
    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._access_token}",
        }

    # ---------- MESSAGES ----------
    async def send_text_message(self, to: str, text: str):
        url = f"{self._base_url}/{self._phone_number_id}/messages"
        logger.info("Enviando mensagem de texto | contact_ref={}", safe_identifier(to))
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }
        return await self._safe_request("POST", url, headers=self._headers(), json=payload)

    async def send_document_message(
        self, to: str, link: str, filename: str | None = None, caption: str | None = None
    ):
        url = f"{self._base_url}/{self._phone_number_id}/messages"
        logger.info(
            "Enviando documento | contact_ref={} document_url={}",
            safe_identifier(to),
            safe_url(link),
        )
        document: dict = {"link": link}
        if filename:
            document["filename"] = filename
        if caption:
            document["caption"] = caption
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "document",
            "document": document,
        }
        return await self._safe_request("POST", url, headers=self._headers(), json=payload)
