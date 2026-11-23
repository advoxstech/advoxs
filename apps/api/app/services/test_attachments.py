"""Ingestão de anexo (PDF/DOCX/TXT) enviado numa conversa de TESTE — mesma
lógica de apps/worker/app/tasks/attachments.py (conversas reais via
WhatsApp), com uma diferença: aqui o arquivo já chega no corpo da própria
requisição HTTP (sem download de Meta/Z-API), e a ingestão roda dentro do
`api`, não como job do `worker`. Extensão como fonte da verdade — mesmo
critério de app/api/v1/knowledge_base.py::ALLOWED_EXTENSIONS.

Duas etapas: `read_test_attachment` valida e lê o arquivo enquanto a
requisição está aberta; `ingest_test_attachment` envia ao api_rag e pode
continuar depois da resposta, quando o documento é grande (ver
app/services/test_conversations.py)."""

import logging
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.clients.rag import RagApiError, insert_user_document_reading
from app.core.config import settings
from app.services.upload_storage import UploadTooLargeError, read_upload_limited

logger = logging.getLogger(__name__)

_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}

_NOTA_FORMATO_NAO_SUPORTADO = (
    "[Anexo recebido mas não pôde ser processado: formato não suportado — envie PDF, DOCX ou TXT.]"
)


def _nota_arquivo_grande() -> str:
    limite_mb = settings.kb_max_file_size_bytes // (1024 * 1024)
    return (
        f"[Anexo recebido mas não pôde ser processado: arquivo excede o limite de {limite_mb} MB.]"
    )


_NOTA_FALHA_INGESTAO = (
    "[Anexo recebido mas não pôde ser processado: falha ao processar o documento.]"
)

# Motivo da recusa, para o agente explicar ao cliente o que fazer
# (_ATTACHMENT_RULE em apps/agents/agents/nodes.py). Mesmo texto em
# apps/worker/app/tasks/attachments.py.
REFUSAL_NOTES = {
    "senha": "[Anexo recebido mas não pôde ser lido: o PDF está protegido por senha.]",
    "corrompido": (
        "[Anexo recebido mas não pôde ser lido: o arquivo está corrompido ou danificado e "
        "não abre.]"
    ),
    "paginas_demais": (
        "[Anexo recebido mas não pôde ser lido: o documento tem mais de 3.000 páginas, acima "
        "do limite de leitura.]"
    ),
    "escaneadas_demais": (
        "[Anexo recebido mas não pôde ser lido: é um documento digitalizado (páginas em "
        "imagem) grande demais para a leitura de imagem. Trate como problema de qualidade do "
        "arquivo: peça o PDF original digital ou o arquivo dividido em partes menores.]"
    ),
    "sem_texto": (
        "[Anexo recebido mas não pôde ser lido: não foi possível ler o texto nem pela leitura "
        "de imagem — qualidade ruim da digitalização ou da foto.]"
    ),
    "leitura_demorada": (
        "[Anexo recebido mas não pôde ser lido: a leitura do documento digitalizado não "
        "terminou no tempo limite — qualidade ou tamanho do arquivo.]"
    ),
}

SCANNED_NOTE = (
    " O documento é digitalizado e foi lido como imagem: trechos com carimbo por cima do "
    "texto, manchas ou baixa qualidade podem não ter sido lidos. Se a informação pedida não "
    "estiver nos trechos encontrados, explique isso ao cliente e peça uma cópia melhor."
)

# Mesmo prazo do worker (apps/worker/app/tasks/attachments.py): um PDF escaneado
# grande leva minutos no api_rag.
ATTACHMENT_RAG_TIMEOUT_SECONDS = 1500


@dataclass
class UploadedAttachment:
    filename: str
    file_bytes: bytes


async def read_test_attachment(file: UploadFile, *, tenant_id: str) -> UploadedAttachment | str:
    """Valida e lê o anexo. Devolve o arquivo lido, ou a nota de recusa para o agente."""
    filename = file.filename or ""
    extension = Path(filename).suffix.lower()
    if extension not in _ALLOWED_EXTENSIONS:
        logger.info(
            "Anexo de teste com formato não suportado | tenant=%s ext=%s", tenant_id, extension
        )
        return _NOTA_FORMATO_NAO_SUPORTADO
    try:
        file_bytes = await read_upload_limited(file, settings.kb_max_file_size_bytes)
    except UploadTooLargeError:
        logger.info("Anexo de teste excede o limite de tamanho | tenant=%s", tenant_id)
        return _nota_arquivo_grande()
    return UploadedAttachment(filename=filename, file_bytes=file_bytes)


async def ingest_test_attachment(
    attachment: UploadedAttachment,
    *,
    tenant_id: str,
    conversation_id: str,
    message_id: str,
) -> str:
    """Ingere o anexo na base pessoal da conversa de teste e devolve a nota para o
    agente (sucesso, leitura parcial ou falha). Nunca levanta.

    `conversation_id` precisa ser só o contact_phone_number da conversa — NUNCA
    o thread_id composto "{tenant_id}:{contact_phone_number}" (ver
    app/clients/rag.py::insert_user_document pro porquê)."""
    try:
        reading = await insert_user_document_reading(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            doc_id=message_id,
            filename=attachment.filename,
            file_bytes=attachment.file_bytes,
            timeout=ATTACHMENT_RAG_TIMEOUT_SECONDS,
        )
    except RagApiError as exc:
        logger.warning(
            "Falha ao ingerir anexo de teste no api_rag | tenant=%s erro=%s", tenant_id, exc
        )
        return REFUSAL_NOTES.get(exc.motivo or "", _NOTA_FALHA_INGESTAO)
    return processed_note(attachment.filename, reading["partial_pages"], reading["ocr_pages"])


async def process_test_attachment(
    file: UploadFile,
    *,
    tenant_id: str,
    conversation_id: str,
    message_id: str,
) -> str:
    """Lê e ingere o anexo de uma vez (as duas etapas acima). Nunca levanta."""
    attachment = await read_test_attachment(file, tenant_id=tenant_id)
    if isinstance(attachment, str):
        return attachment
    return await ingest_test_attachment(
        attachment, tenant_id=tenant_id, conversation_id=conversation_id, message_id=message_id
    )


def processed_note(filename: str, partial_pages: list[int] | None, ocr_pages: int = 0) -> str:
    """Nota para o agente; cita as páginas com leitura parcial, se houver.

    Mesmo texto de apps/worker/app/tasks/attachments.py::processed_note.
    """
    if not partial_pages:
        if ocr_pages:
            return (
                f"[Documento recebido e processado: {filename} — disponível para busca."
                f"{SCANNED_NOTE}]"
            )
        return f"[Documento recebido e processado: {filename} — disponível para busca.]"
    pages = ", ".join(str(p) for p in partial_pages[:30]) + ("…" if len(partial_pages) > 30 else "")
    return (
        f"[Documento recebido e processado parcialmente: {filename} — disponível para busca, "
        f"mas a leitura falhou ou ficou ruim nas páginas {pages} (digitalização de baixa "
        "qualidade). Se a informação pedida não estiver nos trechos encontrados, explique ao "
        "cliente que essas páginas não puderam ser lidas e peça o trecho ou uma cópia mais "
        "nítida; não invente o conteúdo.]"
    )
