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

import logging

import httpx

from app.clients.media import MediaDownloadError, download_meta_media, download_zapi_media
from app.clients.rag import ingest_document
from app.config import settings

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
_NOTA_ARQUIVO_GRANDE = (
    "[Anexo recebido mas não pôde ser processado: arquivo excede o limite de 20 MB.]"
)
_NOTA_FALHA_INGESTAO = (
    "[Anexo recebido mas não pôde ser processado: falha ao processar o documento.]"
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
) -> str | None:
    """Baixa e ingere o anexo, se houver e o formato for suportado.

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
        if whatsapp_provider == "zapi":
            file_bytes = await download_zapi_media(media_ref, zapi_client_token)
        else:
            file_bytes = await download_meta_media(media_ref, access_token or "")
    except MediaDownloadError as exc:
        logger.warning("Falha ao baixar anexo | tenant=%s erro=%s", tenant_id, exc)
        return _NOTA_FALHA_DOWNLOAD

    if len(file_bytes) > settings.attachment_max_bytes:
        logger.info(
            "Anexo excede o limite de tamanho | tenant=%s bytes=%s", tenant_id, len(file_bytes)
        )
        return _NOTA_ARQUIVO_GRANDE

    filename = f"anexo-{message_id}.{extension}"
    try:
        await ingest_document(
            rag_http,
            tenant_id=tenant_id,
            doc_id=message_id,
            filename=filename,
            file_bytes=file_bytes,
            conversation_id=conversation_id,
        )
    except httpx.HTTPError as exc:
        logger.warning("Falha ao ingerir anexo no api_rag | tenant=%s erro=%s", tenant_id, exc)
        return _NOTA_FALHA_INGESTAO

    logger.info(
        "Anexo ingerido | tenant=%s conversation=%s message=%s",
        tenant_id,
        conversation_id,
        message_id,
    )
    return f"[Documento recebido e processado: {filename} — disponível para busca.]"
