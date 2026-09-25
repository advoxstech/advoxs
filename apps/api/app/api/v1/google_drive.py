"""Importação manual: seleção no Google, download limitado e ingestão existente."""

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.google_drive import DriveError, GoogleDrive
from app.core.config import settings
from app.core.queue import get_arq_pool
from app.models import Agent, AgentKnowledgeBaseFile, KnowledgeBaseFile, Tenant
from app.schemas.knowledge_base import DriveImport, DriveSelection
from app.services.subscriptions import get_active_subscription
from app.services.upload_storage import (
    InvalidUploadContentError,
    atomic_write,
    managed_path,
    safe_delete,
    validate_upload_content,
)

router = APIRouter(prefix="/knowledge-base/drive", tags=["knowledge-base"])


def configured() -> bool:
    return settings.google_drive_enabled and all(
        value.strip()
        for value in (
            settings.google_drive_client_id,
            settings.google_drive_api_key,
            settings.google_drive_project_number,
        )
    )


def require_drive() -> None:
    if not configured():
        raise HTTPException(503, "A importação do Google Drive ainda não foi configurada.")


@router.get("/config")
async def config(ctx: TenantContext = Depends(get_current_tenant)) -> dict:
    if not configured():
        return {"enabled": False}
    return {
        "enabled": True,
        "client_id": settings.google_drive_client_id,
        "api_key": settings.google_drive_api_key,
        "project_number": settings.google_drive_project_number,
    }


async def current_file(session: AsyncSession, tenant_id: uuid.UUID, drive_id: str):
    return await session.scalar(
        select(KnowledgeBaseFile).where(
            KnowledgeBaseFile.tenant_id == tenant_id,
            KnowledgeBaseFile.drive_file_id == drive_id,
            KnowledgeBaseFile.superseded_at.is_(None),
            KnowledgeBaseFile.replaces_file_id.is_(None),
        )
    )


@router.post("/preview", dependencies=[Depends(require_drive)])
async def preview(
    body: DriveSelection,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as http:
            file = await GoogleDrive(http, body.access_token.get_secret_value()).metadata(
                body.file_id
            )
    except DriveError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(503, "Não foi possível consultar o Drive. Tente novamente.") from None
    existing = await current_file(session, ctx.tenant_id, file.id)
    pending = None
    if existing:
        pending = await session.scalar(
            select(KnowledgeBaseFile.id).where(
                KnowledgeBaseFile.tenant_id == ctx.tenant_id,
                KnowledgeBaseFile.replaces_file_id == existing.id,
            )
        )
    action = "import"
    if existing:
        if pending or existing.status != "ready":
            action = "pending"
        elif existing.drive_version == file.version:
            action = "unchanged"
        else:
            action = "update"
    return {
        "file_id": file.id,
        "filename": file.filename,
        "version": file.version,
        "action": action,
        "existing_file_id": str(existing.id) if existing else None,
    }


@router.post("/import", status_code=202, dependencies=[Depends(require_drive)])
async def import_file(
    body: DriveImport,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
    arq: ArqRedis = Depends(get_arq_pool),
) -> dict:
    # Verifica o destino e o plano antes de acessar documentos privados.
    agent = await session.scalar(
        select(Agent).where(
            Agent.id == body.agent_id,
            Agent.tenant_id == ctx.tenant_id,
        )
    )
    if agent is None:
        raise HTTPException(404, "Agente não encontrado.")
    subscription, plan = await get_active_subscription(session, ctx.tenant_id)
    if subscription.status != "active":
        raise HTTPException(409, "Regularize a assinatura para importar documentos.")
    try:
        async with (
            asyncio.timeout(90),
            httpx.AsyncClient(timeout=30, follow_redirects=False) as http,
        ):
            drive = GoogleDrive(http, body.access_token.get_secret_value())
            file = await drive.metadata(body.file_id)
            if file.version != body.expected_version:
                raise DriveError("O documento mudou. Selecione-o novamente antes de importar.", 409)
            data = await drive.download(file)
            validate_upload_content(data, Path(file.filename).suffix.lower())
    except DriveError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except InvalidUploadContentError as exc:
        raise HTTPException(400, str(exc)) from None
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError):
        raise HTTPException(503, "Falha ao baixar o documento. Tente novamente.") from None

    # Serializa a reserva de cota do tenant, também usada pelo upload manual.
    await session.execute(select(Tenant.id).where(Tenant.id == ctx.tenant_id).with_for_update())
    existing = await current_file(session, ctx.tenant_id, file.id)
    digest = hashlib.sha256(data).hexdigest()
    if existing:
        if existing.status != "ready":
            raise HTTPException(
                409, "O arquivo já foi importado. Confira o processamento na lista."
            )
        if existing.mime_type != file.mime_type:
            raise HTTPException(
                409, "O formato mudou no Drive. Importe uma nova cópia para preservar o original."
            )
        if existing.drive_version == file.version or existing.content_sha256 == digest:
            if existing.drive_version != file.version:
                existing.drive_version = file.version
                await session.commit()
            return {"result": "unchanged", "file_id": str(existing.id)}
        if body.replace_file_id != existing.id:
            raise HTTPException(409, "Confirme a atualização do documento já importado.")
        pending = await session.scalar(
            select(KnowledgeBaseFile.id).where(
                KnowledgeBaseFile.tenant_id == ctx.tenant_id,
                KnowledgeBaseFile.replaces_file_id == existing.id,
            )
        )
        if pending:
            raise HTTPException(409, "Já existe uma atualização. Reprocesse ou exclua a tentativa.")
    elif body.replace_file_id is not None:
        raise HTTPException(409, "O documento foi alterado ou excluído. Selecione-o novamente.")

    # Atualização mantém o nome local; renomear no Drive não quebra as referências.
    filename = existing.filename if existing else file.filename
    if not existing:
        duplicate = await session.scalar(
            select(KnowledgeBaseFile.id).where(
                KnowledgeBaseFile.tenant_id == ctx.tenant_id,
                KnowledgeBaseFile.filename == filename,
                KnowledgeBaseFile.superseded_at.is_(None),
                KnowledgeBaseFile.replaces_file_id.is_(None),
            )
        )
        if duplicate:
            raise HTTPException(409, "Já existe outro arquivo com esse nome. Renomeie-o no Drive.")
    # Histórico permanece armazenado para fontes antigas e conta na cota de bytes.
    used = await session.scalar(
        select(func.coalesce(func.sum(KnowledgeBaseFile.size_bytes), 0)).where(
            KnowledgeBaseFile.tenant_id == ctx.tenant_id,
        )
    )
    if plan.max_knowledge_base_storage_bytes is not None:
        if used + len(data) > plan.max_knowledge_base_storage_bytes:
            raise HTTPException(
                413, "Limite de armazenamento atingido, incluindo versões anteriores."
            )
    if not existing and plan.max_knowledge_base_files is not None:
        count = await session.scalar(
            select(func.count())
            .select_from(KnowledgeBaseFile)
            .where(
                KnowledgeBaseFile.tenant_id == ctx.tenant_id,
                KnowledgeBaseFile.superseded_at.is_(None),
                KnowledgeBaseFile.replaces_file_id.is_(None),
            )
        )
        if count >= plan.max_knowledge_base_files:
            raise HTTPException(409, "O limite de arquivos do plano foi atingido.")
    record = KnowledgeBaseFile(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        filename=filename,
        size_bytes=len(data),
        mime_type=file.mime_type,
        status="processing",
        category=existing.category if existing else body.category,
        drive_file_id=file.id,
        drive_version=file.version,
        content_sha256=digest,
        imported_at=datetime.now(UTC),
        replaces_file_id=existing.id if existing else None,
    )
    try:
        session.add(record)
        await session.flush()
        if not existing:
            session.add(
                AgentKnowledgeBaseFile(agent_id=body.agent_id, knowledge_base_file_id=record.id)
            )
        atomic_write(managed_path(settings.kb_upload_dir, str(ctx.tenant_id), str(record.id)), data)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        safe_delete(settings.kb_upload_dir, str(ctx.tenant_id), str(record.id))
        if isinstance(exc, IntegrityError):
            raise HTTPException(
                409, "O arquivo já está sendo importado. Atualize a lista."
            ) from None
        raise
    try:
        await arq.enqueue_job(
            "ingest_knowledge_base_file",
            tenant_id=str(ctx.tenant_id),
            file_id=str(record.id),
            _job_id=f"kb:{record.id}",
        )
    except Exception:
        record.status = "error"
        record.error_message = "Não foi possível iniciar o processamento. Clique em Reprocessar."
        await session.commit()
        return {"result": "error", "file_id": str(record.id), "message": record.error_message}
    return {"result": "processing", "file_id": str(record.id)}
