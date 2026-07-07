import httpx
from dotenv import load_dotenv
import os
import time
from loguru import logger

load_dotenv()

GRAPH_API_BASE_URL = os.getenv("GRAPH_API_BASE_URL", "https://graph.facebook.com")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v23.0")


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
            "WhatsAppClient inicializado | phone_number_id={}", phone_number_id
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
        started_at = time.perf_counter()
        client = self._get_client()
        try:
            logger.info(
                "Executando requisição à Graph API | method={} url={}", method, url
            )
            response = await client.request(method, url, **kwargs)

            if response.is_error:
                logger.warning(
                    "Resposta HTTP não OK | method={} url={} status={} body={}",
                    method, url, response.status_code, response.text,
                )
                return {
                    "success": False,
                    "data": None,
                    "error": f"HTTP {response.status_code}: {response.text}",
                }

            try:
                data = response.json()
            except Exception:
                data = response.text

            elapsed = round(time.perf_counter() - started_at, 3)
            logger.info(
                "Requisição concluída | method={} url={} status={} elapsed={}s",
                method, url, response.status_code, elapsed,
            )
            return {"success": True, "data": data, "error": None}

        except httpx.TimeoutException:
            logger.error("Timeout ao acessar Graph API | method={} url={}", method, url)
            return {"success": False, "data": None, "error": "Timeout ao acessar Graph API"}

        except httpx.ConnectError as e:
            logger.error(
                "Erro de conexão com Graph API | method={} url={} error={}", method, url, e
            )
            return {"success": False, "data": None, "error": f"Erro de conexão: {e}"}

        except httpx.RequestError as e:
            logger.error(
                "Erro de requisição à Graph API | method={} url={} error={}", method, url, e
            )
            return {"success": False, "data": None, "error": f"Erro de requisição: {e}"}

        except Exception as e:
            logger.exception(
                "Erro inesperado ao acessar Graph API | method={} url={}", method, url
            )
            return {"success": False, "data": None, "error": f"Erro inesperado: {e}"}

    # ---------- HEADERS ----------
    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._access_token}",
        }

    # ---------- MESSAGES ----------
    async def send_text_message(self, to: str, text: str):
        url = f"{self._base_url}/{self._phone_number_id}/messages"
        logger.info("Enviando mensagem de texto | to={}", to)
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
        logger.info("Enviando documento | to={} link={}", to, link)
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
