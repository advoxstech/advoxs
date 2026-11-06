"""Ingestão de anexo (PDF/DOCX/TXT) recebido do contato via WhatsApp, na base
de conhecimento PESSOAL dele — a tool `buscar_base_conhecimento_usuario`
(apps/agents/agents/tools.py) já sabe buscar ali por conversation_id, sem
nenhuma mudança do lado do agents.

Só documento é suportado (PDF/DOCX/TXT) — o api_rag não processa imagem/
áudio/vídeo hoje (sem OCR, ver apps/api_rag/services/documents/main.py).
Chamado de dentro de process_inbound_message, ANTES da chamada ao agents —
pra o documento já estar pesquisável na mesma resposta, o download+ingestão
precisa terminar antes, não depois (best-effort: nunca levanta, qualquer
falha vira uma nota de texto anexada à mensagem do usuário)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx

from app.clients.media import (
    MediaDownloadError,
    MediaTooLargeError,
    download_meta_media,
    download_zapi_media,
)
from app.clients.rag import ingest_document
from app.config import settings
from app.safe_logging import safe_error

logger = logging.getLogger(__name__)

# Únicos formatos que o api_rag sabe processar hoje (_extrair_texto em
# apps/api_rag/services/documents/main.py) — chave = media_type persistido em
# messages.media_type (mime_type da Meta/Z-API, com fallback pro "kind" ex:
# "document"/"image" quando o provedor não manda o mime type).
_SUPPORTED_MIME_EXTENSIONS = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
}

_NOTA_FORMATO_NAO_SUPORTADO = (
    "[Anexo recebido mas não pôde ser processado: formato não suportado — envie PDF, DOCX ou TXT.]"
)
_NOTA_FALHA_DOWNLOAD = "[Anexo recebido mas não pôde ser processado: falha ao baixar o arquivo.]"


def _nota_arquivo_grande() -> str:
    limite_mb = settings.attachment_max_bytes // (1024 * 1024)
    return (
        f"[Anexo recebido mas não pôde ser processado: arquivo excede o limite de {limite_mb} MB.]"
    )


_NOTA_FALHA_INGESTAO = (
    "[Anexo recebido mas não pôde ser processado: falha ao processar o documento.]"
)

# Um PDF escaneado grande leva minutos no api_rag (OCR). A leitura espera até
# ATTACHMENT_RAG_TIMEOUT_SECONDS, e o contato é avisado se ela passar de
# ATTACHMENT_NOTICE_AFTER_SECONDS. O job do turno (worker.py) e o bloqueio da
# conversa (PROCESSING_LEASE) precisam durar mais que essa espera.
ATTACHMENT_NOTICE_AFTER_SECONDS = 45
ATTACHMENT_RAG_TIMEOUT_SECONDS = 1500

READING_NOTICE = (
    "Recebi seu documento. Ele é grande e vou levar alguns minutos para ler; "
    "assim que terminar, respondo sua mensagem."
)

# Acrescentada à mensagem do contato quando o aviso acima saiu: o agente não
# viu o aviso (texto fixo, fora do LLM) e precisa saber que ficou devendo a
# resposta — senão, se o cliente mandou só "Ok" enquanto esperava, ele tende a
# responder apenas o "Ok".
READING_NOTICE_AGENT_NOTE = (
    "[A leitura do documento demorou e o cliente recebeu o aviso automático: "
    f'"{READING_NOTICE}" Responda agora ao que ele pediu junto com o documento, '
    "considerando também as mensagens que ele mandou enquanto esperava.]"
)


async def process_inbound_attachment(
    rag_http: httpx.AsyncClient,
    *,
    tenant_id: str,
    conversation_id: str,
    message_id: str,
    media_ref: str | None,
    media_type: str | None,
    whatsapp_provider: str,
    access_token: str | None,
    zapi_client_token: str | None = None,
    on_slow_reading: Callable[[], Awaitable[None]] | None = None,
) -> str | None:
    """Baixa e ingere o anexo, se houver e o formato for suportado.

    `on_slow_reading` é chamado uma vez se a leitura passar de
    ATTACHMENT_NOTICE_AFTER_SECONDS (aviso ao contato); a leitura continua até
    o fim. Falha no aviso nunca interrompe a leitura.

    `media_ref` é o que está gravado em messages.media_url — pra Meta, o
    media_id opaco (exige download autenticado com `access_token`); pra
    Z-API, a própria URL final do arquivo (exige o `zapi_client_token` da
    conta no header, ver app/clients/media.py::download_zapi_media).

    Devolve uma nota em texto pra anexar à mensagem mandada ao agents
    (sucesso ou falha), ou None quando não havia anexo nenhum pra processar.
    Nunca levanta.
    """
    if not media_ref:
        return None

    extension = _SUPPORTED_MIME_EXTENSIONS.get(media_type or "")
    if extension is None:
        logger.info(
            "Anexo com formato não suportado, ignorado | tenant=%s tipo=%s", tenant_id, media_type
        )
        return _NOTA_FORMATO_NAO_SUPORTADO

    try:
        if whatsapp_provider in ("zapi", "omni"):
            file_bytes = await download_zapi_media(
                media_ref,
                zapi_client_token,
                settings.attachment_max_bytes,
                **({"omni": True} if whatsapp_provider == "omni" else {}),
            )
        else:
            file_bytes = await download_meta_media(
                media_ref, access_token or "", settings.attachment_max_bytes
            )
    except MediaTooLargeError:
        logger.info("Anexo excede o limite de tamanho | tenant=%s", tenant_id)
        return _nota_arquivo_grande()
    except MediaDownloadError as exc:
        logger.warning(
            "Falha ao baixar anexo | tenant=%s error_type=%s", tenant_id, safe_error(exc)
        )
        return _NOTA_FALHA_DOWNLOAD

    filename = f"anexo-{message_id}.{extension}"
    ingestion = ingest_document(
        rag_http,
        tenant_id=tenant_id,
        doc_id=message_id,
        filename=filename,
        file_bytes=file_bytes,
        conversation_id=conversation_id,
        timeout=ATTACHMENT_RAG_TIMEOUT_SECONDS,
    )
    try:
        reading = await await_with_notice(ingestion, on_slow_reading, tenant_id=tenant_id)
    except httpx.HTTPError as exc:
        logger.warning(
            "Falha ao ingerir anexo no api_rag | tenant=%s error_type=%s",
            tenant_id,
            safe_error(exc),
        )
        return _NOTA_FALHA_INGESTAO

    logger.info(
        "Anexo ingerido | tenant=%s conversation=%s message=%s",
        tenant_id,
        conversation_id,
        message_id,
    )
    return processed_note(filename, reading)


async def await_with_notice(
    work: Awaitable,
    on_slow: Callable[[], Awaitable[None]] | None,
    *,
    tenant_id: str,
    notice_after: float | None = None,
):
    """Espera `work` até o fim; se passar de `notice_after`, chama `on_slow` uma vez.

    Falha no aviso só é registrada. Se a espera for cancelada (prazo do job),
    o trabalho também é cancelado, para não ficar órfão.
    """
    if notice_after is None:
        notice_after = ATTACHMENT_NOTICE_AFTER_SECONDS
    task = asyncio.ensure_future(work)
    try:
        done, _ = await asyncio.wait({task}, timeout=notice_after)
        if not done and on_slow is not None:
            logger.info("Leitura de anexo demorada, avisando o contato | tenant=%s", tenant_id)
            try:
                await on_slow()
            except Exception as exc:  # aviso é best-effort
                logger.warning(
                    "Falha ao avisar leitura demorada | tenant=%s error_type=%s",
                    tenant_id,
                    safe_error(exc),
                )
        return await task
    finally:
        if not task.done():
            task.cancel()


def processed_note(filename: str, reading: dict | None) -> str:
    """Nota para o agente; cita as páginas com leitura parcial, se houver."""
    partial = (reading or {}).get("partial_pages") or []
    if not partial:
        return f"[Documento recebido e processado: {filename} — disponível para busca.]"
    pages = ", ".join(str(p) for p in partial[:30]) + ("…" if len(partial) > 30 else "")
    return (
        f"[Documento recebido e processado parcialmente: {filename} — disponível para busca, "
        f"mas a leitura falhou ou ficou ruim nas páginas {pages} (digitalização de baixa "
        "qualidade). Se a informação pedida não estiver nos trechos encontrados, explique ao "
        "cliente que essas páginas não puderam ser lidas e peça o trecho ou uma cópia mais "
        "nítida; não invente o conteúdo.]"
    )
