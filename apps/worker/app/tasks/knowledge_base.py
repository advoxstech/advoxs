import logging
import uuid
from pathlib import Path

import httpx
from arq.worker import Retry
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import tables
from app.clients.rag import ingest_document
from app.config import settings

logger = logging.getLogger(__name__)

# Na última tentativa, marca error em vez de reagendar (o default de
# max_tries do Arq também é 5 — manter em sincronia).
MAX_TRIES = 5


async def ingest_knowledge_base_file(ctx: dict, tenant_id: str, file_id: str) -> None:
    """Lê o arquivo do volume compartilhado, ingere no api_rag e marca o status.

    Idempotente: retries re-checam o status antes de reprocessar, e o api_rag
    substitui documento re-ingerido com o mesmo doc_id.
    """
    session_factory = ctx["session_factory"]
    http: httpx.AsyncClient = ctx["rag_http"]

    async with session_factory() as session:
        row = await _load_file(session, file_id)

    if row is None or row.status != "processing":
        logger.info("Arquivo inexistente ou já processado | file=%s", file_id)
        return

    path = Path(settings.kb_upload_dir) / tenant_id / file_id
    if not path.exists():
        await _set_status(session_factory, file_id, "error", "Arquivo temporário não encontrado")
        return

    try:
        await ingest_document(
            http,
            tenant_id=tenant_id,
            doc_id=file_id,
            filename=row.filename,
            file_bytes=path.read_bytes(),
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500 and ctx.get("job_try", 1) < MAX_TRIES:
            logger.warning("api_rag 5xx, reagendando | file=%s", file_id)
            raise Retry(defer=ctx.get("job_try", 1) * 15)
        await _set_status(
            session_factory,
            file_id,
            "error",
            f"Falha na ingestão (HTTP {exc.response.status_code})",
        )
        return
    except httpx.HTTPError as exc:
        if ctx.get("job_try", 1) < MAX_TRIES:
            logger.warning("api_rag indisponível, reagendando | file=%s erro=%s", file_id, exc)
            raise Retry(defer=ctx.get("job_try", 1) * 15)
        await _set_status(session_factory, file_id, "error", "Serviço de ingestão indisponível")
        return

    await _set_status(session_factory, file_id, "ready", None)
    path.unlink(missing_ok=True)
    logger.info("Arquivo ingerido | tenant=%s file=%s", tenant_id, file_id)


async def _load_file(session: AsyncSession, file_id: str):
    return (
        await session.execute(
            select(
                tables.knowledge_base_files.c.filename,
                tables.knowledge_base_files.c.status,
            ).where(tables.knowledge_base_files.c.id == uuid.UUID(file_id))
        )
    ).one_or_none()


async def _set_status(
    session_factory, file_id: str, status: str, error_message: str | None
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(tables.knowledge_base_files)
            .where(tables.knowledge_base_files.c.id == uuid.UUID(file_id))
            .values(status=status, error_message=error_message)
        )
        await session.commit()
