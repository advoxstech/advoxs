"""Storage local dos PDFs gerados pelas tools de documento (agents/tools.py).

Substitui o upload no Google Drive que o fluxo n8n original fazia: o PDF é
gravado num volume compartilhado e servido por uma rota própria do `agents`
(api/routes.py, GET /generated-documents/{doc_id}) — o link resultante é o que
send_document_message (Meta/Z-API) usa pra entregar o documento ao contato.

Retenção curta (24h, ver RETENTION_HOURS): o WhatsApp/Z-API busca o link quase
na hora do envio, então é seguro apagar depois de um dia — evita crescimento
ilimitado do volume sem precisar de um serviço de limpeza à parte.
"""

import asyncio
import os
import time
import uuid

from loguru import logger

from clients.document_generation import DocumentGenerationError

GENERATED_DOCUMENTS_DIR = os.getenv("GENERATED_DOCUMENTS_DIR", "/data/generated_documents")
AGENTS_PUBLIC_URL = os.getenv("AGENTS_PUBLIC_URL", "")

RETENTION_HOURS = 24
_CLEANUP_INTERVAL_SECONDS = 60 * 60


def _ensure_dir() -> None:
    os.makedirs(GENERATED_DOCUMENTS_DIR, exist_ok=True)


def save_pdf(pdf_bytes: bytes) -> str:
    """Grava o PDF com um nome aleatório e devolve o doc_id (sem extensão)."""
    _ensure_dir()
    doc_id = uuid.uuid4().hex
    path = os.path.join(GENERATED_DOCUMENTS_DIR, f"{doc_id}.pdf")
    with open(path, "wb") as f:
        f.write(pdf_bytes)
    logger.info("PDF gerado salvo | doc_id={} tamanho_bytes={}", doc_id, len(pdf_bytes))
    return doc_id


def build_public_url(doc_id: str) -> str:
    if not AGENTS_PUBLIC_URL:
        raise DocumentGenerationError(
            "Falha ao gerar o link do documento: AGENTS_PUBLIC_URL não configurada."
        )
    return f"{AGENTS_PUBLIC_URL.rstrip('/')}/generated-documents/{doc_id}"


def resolve_path(doc_id: str) -> str | None:
    """Valida o formato do doc_id (hex de UUID) antes de montar o path — a
    rota que serve o arquivo passa o valor recebido na URL direto pra cá, sem
    isso um doc_id malicioso poderia tentar path traversal."""
    try:
        uuid.UUID(hex=doc_id)
    except ValueError:
        return None
    path = os.path.join(GENERATED_DOCUMENTS_DIR, f"{doc_id}.pdf")
    return path if os.path.isfile(path) else None


def cleanup_old_files(max_age_hours: int = RETENTION_HOURS) -> int:
    if not os.path.isdir(GENERATED_DOCUMENTS_DIR):
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for name in os.listdir(GENERATED_DOCUMENTS_DIR):
        path = os.path.join(GENERATED_DOCUMENTS_DIR, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError as exc:
            logger.warning("Falha ao limpar documento gerado | path={} erro={}", path, exc)
    if removed:
        logger.info("Limpeza de documentos gerados | removidos={}", removed)
    return removed


async def start_cleanup_loop() -> None:
    """Loop em background (disparado no startup do FastAPI) — apaga PDFs
    gerados com mais de RETENTION_HOURS a cada hora. Best-effort: uma falha
    de limpeza não derruba o serviço, só loga e tenta de novo no próximo ciclo."""
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
        try:
            cleanup_old_files()
        except Exception:
            logger.exception("Erro inesperado na limpeza de documentos gerados")
