import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from arq.worker import Retry
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import tables
from app.clients.rag import ingest_document
from app.config import settings
from app.db import open_system_session, open_tenant_session
from app.safe_logging import safe_error

logger = logging.getLogger(__name__)

# Na última tentativa, marca error em vez de reagendar (o default de
# max_tries do Arq também é 5 — manter em sincronia).
MAX_TRIES = 5


async def recover_drive_imports(ctx: dict) -> None:
    """Recupera importações aceitas antes de queda da API/fila/worker, sem token Google."""
    files = tables.knowledge_base_files
    async with open_system_session(ctx["system_session_factory"]) as session:
        rows = (
            await session.execute(
                select(files.c.id, files.c.tenant_id)
                .where(
                    files.c.drive_file_id.is_not(None),
                    files.c.status == "processing",
                    files.c.imported_at < datetime.now(UTC) - timedelta(minutes=10),
                )
                .order_by(files.c.imported_at)
                .limit(100)
            )
        ).all()
    for row in rows:
        await ctx["redis"].enqueue_job(
            "ingest_knowledge_base_file",
            tenant_id=str(row.tenant_id),
            file_id=str(row.id),
            _job_id=f"kb:{row.id}",
        )


async def ingest_knowledge_base_file(ctx: dict, tenant_id: str, file_id: str) -> None:
    """Lê o arquivo do volume compartilhado, ingere no api_rag e marca o status.

    Idempotente: retries re-checam o status antes de reprocessar, e o api_rag
    substitui documento re-ingerido com o mesmo doc_id.
    """
    session_factory = ctx["session_factory"]
    http: httpx.AsyncClient = ctx["rag_http"]

    async with open_tenant_session(session_factory, tenant_id) as session:
        row = await _load_file(session, file_id)

    if row is None or row.status != "processing":
        logger.info("Arquivo inexistente ou já processado | file=%s", file_id)
        return

    path = Path(settings.kb_upload_dir) / tenant_id / file_id
    if not path.exists():
        await _set_status(
            session_factory, file_id, "error", "Arquivo temporário não encontrado", tenant_id
        )
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
            tenant_id,
        )
        return
    except httpx.HTTPError as exc:
        if ctx.get("job_try", 1) < MAX_TRIES:
            logger.warning(
                "api_rag indisponível, reagendando | file=%s error_type=%s",
                file_id,
                safe_error(exc),
            )
            raise Retry(defer=ctx.get("job_try", 1) * 15)
        await _set_status(
            session_factory, file_id, "error", "Serviço de ingestão indisponível", tenant_id
        )
        return

    if getattr(row, "replaces_file_id", None):
        try:
            await _publish_replacement(session_factory, tenant_id, file_id, row.replaces_file_id)
        except Exception:
            if ctx.get("job_try", 1) < MAX_TRIES:
                raise Retry(defer=ctx.get("job_try", 1) * 15) from None
            await _set_status(
                session_factory,
                file_id,
                "error",
                "Falha ao publicar a atualização. A versão anterior foi preservada.",
                tenant_id,
            )
            return
    else:
        await _set_status(session_factory, file_id, "ready", None, tenant_id)
    path.unlink(missing_ok=True)
    logger.info("Arquivo ingerido | tenant=%s file=%s", tenant_id, file_id)


async def _load_file(session: AsyncSession, file_id: str):
    return (
        await session.execute(
            select(
                tables.knowledge_base_files.c.filename,
                tables.knowledge_base_files.c.status,
                tables.knowledge_base_files.c.replaces_file_id,
            ).where(tables.knowledge_base_files.c.id == uuid.UUID(file_id))
        )
    ).one_or_none()


async def _set_status(
    session_factory, file_id: str, status: str, error_message: str | None, tenant_id: str
) -> None:
    async with open_tenant_session(session_factory, tenant_id) as session:
        await session.execute(
            update(tables.knowledge_base_files)
            .where(tables.knowledge_base_files.c.id == uuid.UUID(file_id))
            .values(status=status, error_message=error_message)
        )
        await session.commit()


async def _publish_replacement(session_factory, tenant_id: str, file_id: str, previous_id) -> None:
    """Publica apenas após ingestão completa, preservando fontes de mensagens antigas.

    A transação troca todos os vínculos de uma vez. O UUID antigo permanece com
    seu próprio original/chunks, mas não está mais na lista permitida dos agentes.
    """
    files = tables.knowledge_base_files
    links = tables.agent_knowledge_base_files
    tid, fid = uuid.UUID(tenant_id), uuid.UUID(file_id)
    async with open_tenant_session(session_factory, tenant_id) as session:
        await session.execute(
            select(tables.tenants.c.id).where(tables.tenants.c.id == tid).with_for_update()
        )
        candidate = (
            await session.execute(
                select(files)
                .where(
                    files.c.id == fid,
                    files.c.tenant_id == tid,
                )
                .with_for_update()
            )
        ).one_or_none()
        if candidate is None or candidate.status != "processing":
            return
        previous = (
            await session.execute(
                select(files)
                .where(
                    files.c.id == previous_id,
                    files.c.tenant_id == tid,
                    files.c.superseded_at.is_(None),
                )
                .with_for_update()
            )
        ).one_or_none()
        if previous is None:
            raise RuntimeError("Versão anterior indisponível para publicação")
        await session.execute(
            update(files)
            .where(
                files.c.id == previous_id,
                files.c.tenant_id == tid,
            )
            .values(superseded_at=func.now())
        )
        await session.execute(
            update(files)
            .where(
                files.c.id == fid,
                files.c.tenant_id == tid,
            )
            .values(
                status="ready",
                error_message=None,
                replaces_file_id=None,
                category=previous.category,
            )
        )
        await session.execute(
            update(links)
            .where(
                links.c.knowledge_base_file_id == previous_id,
            )
            .values(knowledge_base_file_id=fid)
        )
        await session.commit()
