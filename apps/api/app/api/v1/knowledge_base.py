"""Base de conhecimento do escritório: upload, listagem e exclusão de arquivos."""

import logging
import uuid
from pathlib import Path

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.rag import RagApiError, delete_documents
from app.core.config import settings
from app.core.queue import get_arq_pool
from app.models import Agent, AgentKnowledgeBaseFile, KnowledgeBaseFile
from app.schemas.knowledge_base import KnowledgeBaseFileOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])

# A extensão do filename é a fonte da verdade; o mime declarado só precisa
# ser compatível ou genérico.
ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}
GENERIC_MIME_TYPES = {"", "application/octet-stream"}


@router.post("/files", status_code=status.HTTP_202_ACCEPTED)
async def upload_file(
    file: UploadFile = File(...),
    agent_id: uuid.UUID | None = Form(default=None),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
    arq: ArqRedis = Depends(get_arq_pool),
) -> KnowledgeBaseFileOut:
    if agent_id is not None:
        agent = await session.scalar(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == ctx.tenant_id)
        )
        if agent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Agente não encontrado"
            )
    else:
        # Campo opcional pra não quebrar o painel web já em produção (nunca
        # manda agent_id) — cai no ponto de entrada do tenant, aproximando
        # ao máximo o comportamento de "sem conceito de agente" de antes.
        agent = await session.scalar(
            select(Agent).where(Agent.tenant_id == ctx.tenant_id, Agent.is_entry_point.is_(True))
        )
        if agent is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Nenhum agente-destino disponível para o upload — configure um agente",
            )
        agent_id = agent.id

    filename = file.filename or ""
    extension = Path(filename).suffix.lower()
    expected_mime = ALLOWED_EXTENSIONS.get(extension)
    if expected_mime is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato não suportado — envie PDF, DOCX ou TXT",
        )
    declared_mime = file.content_type or ""
    if declared_mime not in GENERIC_MIME_TYPES and declared_mime != expected_mime:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tipo de conteúdo não corresponde à extensão {extension}",
        )

    data = await file.read()
    if len(data) > settings.kb_max_file_size_bytes:
        limite_mb = settings.kb_max_file_size_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Arquivo excede o limite de {limite_mb} MB",
        )

    used = await session.scalar(
        select(func.coalesce(func.sum(KnowledgeBaseFile.size_bytes), 0)).where(
            KnowledgeBaseFile.tenant_id == ctx.tenant_id
        )
    )
    if used + len(data) > settings.kb_max_total_size_bytes:
        remaining = max(settings.kb_max_total_size_bytes - used, 0)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Limite de storage do escritório atingido — restam {remaining} bytes",
        )

    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Arquivo vazio")

    duplicate = await session.scalar(
        select(KnowledgeBaseFile.id).where(
            KnowledgeBaseFile.tenant_id == ctx.tenant_id,
            KnowledgeBaseFile.filename == filename,
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe um arquivo com esse nome — exclua o antigo antes de re-subir",
        )

    record = KnowledgeBaseFile(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        filename=filename,
        size_bytes=len(data),
        mime_type=expected_mime,
        status="processing",
    )
    session.add(record)
    # Flush explícito antes de adicionar o vínculo: sem isso, a FK de
    # agent_knowledge_base_files.knowledge_base_file_id pode ser checada
    # antes do INSERT de knowledge_base_files acontecer de fato (SQLAlchemy
    # não garante ordem entre dois session.add() de tabelas sem relationship()
    # entre si) — violava a constraint em todo upload real contra Postgres,
    # mascarado pelos testes unitários (sessão mockada, sem FK de verdade).
    await session.flush()
    session.add(AgentKnowledgeBaseFile(agent_id=agent_id, knowledge_base_file_id=record.id))

    tenant_dir = Path(settings.kb_upload_dir) / str(ctx.tenant_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    (tenant_dir / str(record.id)).write_bytes(data)

    try:
        await session.commit()
    except IntegrityError:
        # Corrida entre uploads concorrentes com o mesmo filename — a unique
        # constraint (tenant_id, filename) é o backstop do check acima.
        await session.rollback()
        (tenant_dir / str(record.id)).unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe um arquivo com esse nome — exclua o antigo antes de re-subir",
        )
    await session.refresh(record)
    # Enfileira só depois do commit — o worker não pode acordar antes de a
    # linha estar visível (mesmo padrão do webhook do WhatsApp).
    await arq.enqueue_job(
        "ingest_knowledge_base_file",
        tenant_id=str(ctx.tenant_id),
        file_id=str(record.id),
    )
    return KnowledgeBaseFileOut.model_validate(record)


@router.get("/files")
async def list_files(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[KnowledgeBaseFileOut]:
    result = await session.execute(
        select(KnowledgeBaseFile)
        .where(KnowledgeBaseFile.tenant_id == ctx.tenant_id)
        .order_by(KnowledgeBaseFile.uploaded_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [KnowledgeBaseFileOut.model_validate(f) for f in result.scalars().all()]


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    record = await session.scalar(
        select(KnowledgeBaseFile).where(
            KnowledgeBaseFile.id == file_id,
            KnowledgeBaseFile.tenant_id == ctx.tenant_id,
        )
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arquivo não encontrado")
    if record.status == "processing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Arquivo em processamento — aguarde a ingestão terminar para excluir",
        )

    # Remove no api_rag primeiro: se falhar, o registro fica e o usuário
    # tenta de novo (nunca deixa chunk órfão no Qdrant).
    try:
        await delete_documents(str(ctx.tenant_id), [str(file_id)])
    except RagApiError as exc:
        # Detalhe interno só no log — não expor nome/erro do serviço ao tenant.
        logger.error("Falha ao excluir no api_rag | file=%s erro=%s", file_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao excluir o arquivo — tente novamente em instantes",
        )

    temp_path = Path(settings.kb_upload_dir) / str(ctx.tenant_id) / str(file_id)
    temp_path.unlink(missing_ok=True)

    await session.delete(record)
    await session.commit()
