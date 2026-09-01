"""Ingestão de anexo (PDF/DOCX/TXT) enviado numa conversa de TESTE — mesma
lógica de apps/worker/app/tasks/attachments.py (conversas reais via
WhatsApp), com uma diferença: aqui o arquivo já chega no corpo da própria
requisição HTTP (sem download de Meta/Z-API), e a ingestão roda síncrona
dentro do `api`, não como job do `worker`. Extensão como fonte da verdade —
mesmo critério de app/api/v1/knowledge_base.py::ALLOWED_EXTENSIONS."""

import logging
from pathlib import Path

from fastapi import UploadFile

from app.clients.rag import RagApiError, insert_user_document
from app.core.config import settings

logger = logging.getLogger(__name__)

_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}

_NOTA_FORMATO_NAO_SUPORTADO = (
    "[Anexo recebido mas não pôde ser processado: formato não suportado — envie PDF, DOCX ou TXT.]"
)
_NOTA_ARQUIVO_GRANDE = (
    "[Anexo recebido mas não pôde ser processado: arquivo excede o limite de 20 MB.]"
)
_NOTA_FALHA_INGESTAO = (
    "[Anexo recebido mas não pôde ser processado: falha ao processar o documento.]"
)


async def process_test_attachment(
    file: UploadFile,
    *,
    tenant_id: str,
    conversation_id: str,
    message_id: str,
) -> str:
    """Valida e ingere o anexo na base pessoal da conversa de teste.
    `conversation_id` precisa ser só o contact_phone_number da conversa —
    NUNCA o thread_id composto "{tenant_id}:{contact_phone_number}" (ver
    app/clients/rag.py::insert_user_document pro porquê). Devolve uma nota em
    texto pra anexar à mensagem mandada ao agents (sucesso ou falha). Nunca
    levanta."""
    filename = file.filename or ""
    extension = Path(filename).suffix.lower()
    if extension not in _ALLOWED_EXTENSIONS:
        logger.info(
            "Anexo de teste com formato não suportado | tenant=%s ext=%s", tenant_id, extension
        )
        return _NOTA_FORMATO_NAO_SUPORTADO

    file_bytes = await file.read()
    if len(file_bytes) > settings.kb_max_file_size_bytes:
        logger.info(
            "Anexo de teste excede o limite de tamanho | tenant=%s bytes=%s",
            tenant_id,
            len(file_bytes),
        )
        return _NOTA_ARQUIVO_GRANDE

    try:
        await insert_user_document(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            doc_id=message_id,
            filename=filename,
            file_bytes=file_bytes,
        )
    except RagApiError as exc:
        logger.warning(
            "Falha ao ingerir anexo de teste no api_rag | tenant=%s erro=%s", tenant_id, exc
        )
        return _NOTA_FALHA_INGESTAO

    return f"[Documento recebido e processado: {filename} — disponível para busca.]"
