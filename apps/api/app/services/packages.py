"""Pacotes de especialidade: cópias dos documentos da base central no escritório.

Cada documento da central é lido e indexado uma vez (tenant reservado
PUBLIC_TENANT_ID no api_rag). Quando um agente recebe uma especialidade, o
escritório ganha uma cópia de cada documento do pacote: um registro normal em
knowledge_base_files (marcado com public_document_id), que o escritório pode
excluir e que não conta no limite do plano. A cópia do conteúdo já indexado é
feita pelo worker (copy_public_document), sem ler o arquivo de novo.
"""

import logging
import uuid
from pathlib import PurePath

from arq.connections import ArqRedis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.rag import RagApiError, delete_documents
from app.core.queue import INGESTION_QUEUE
from app.core.safe_logging import safe_error
from app.models import (
    Agent,
    AgentKnowledgeBaseFile,
    KnowledgeBaseFile,
    PublicDocument,
    SpecialtyPackageDocument,
)

logger = logging.getLogger(__name__)


def _active(tenant_id: uuid.UUID):
    return (
        KnowledgeBaseFile.tenant_id == tenant_id,
        KnowledgeBaseFile.superseded_at.is_(None),
        KnowledgeBaseFile.replaces_file_id.is_(None),
    )


async def _free_filename(session: AsyncSession, tenant_id: uuid.UUID, filename: str) -> str:
    """O nome é único por escritório: se já existe um arquivo próprio com o mesmo
    nome, a cópia do pacote ganha um sufixo em vez de falhar."""
    taken = await session.scalar(
        select(KnowledgeBaseFile.id).where(
            *_active(tenant_id), KnowledgeBaseFile.filename == filename
        )
    )
    if taken is None:
        return filename
    path = PurePath(filename)
    return f"{path.stem} (base Advoxs){path.suffix}"


async def sync_agent_package(session: AsyncSession, agent: Agent) -> list[uuid.UUID]:
    """Garante no escritório as cópias do pacote da especialidade do agente e as
    liga a ele. Devolve os ids dos arquivos cujo conteúdo precisa ser copiado
    (novos, desatualizados ou com falha) — enfileirar depois do commit.

    Não faz commit. Documentos que o escritório excluiu voltam: é o que o
    "Restaurar pacote" promete.
    """
    if not agent.specialty:
        return []
    documents = (
        await session.scalars(
            select(PublicDocument)
            .join(
                SpecialtyPackageDocument,
                SpecialtyPackageDocument.public_document_id == PublicDocument.id,
            )
            .where(
                SpecialtyPackageDocument.specialty == agent.specialty,
                PublicDocument.status == "ready",
            )
            .order_by(PublicDocument.code)
        )
    ).all()
    if not documents:
        return []

    existing = {
        record.public_document_id: record
        for record in (
            await session.scalars(
                select(KnowledgeBaseFile).where(
                    *_active(agent.tenant_id),
                    KnowledgeBaseFile.public_document_id.in_([d.id for d in documents]),
                )
            )
        ).all()
    }
    linked = set(
        (
            await session.scalars(
                select(AgentKnowledgeBaseFile.knowledge_base_file_id).where(
                    AgentKnowledgeBaseFile.agent_id == agent.id
                )
            )
        ).all()
    )

    to_copy: list[uuid.UUID] = []
    for document in documents:
        record = existing.get(document.id)
        if record is None:
            record = KnowledgeBaseFile(
                id=uuid.uuid4(),
                tenant_id=agent.tenant_id,
                filename=await _free_filename(session, agent.tenant_id, document.filename),
                public_document_id=document.id,
            )
            session.add(record)
            outdated = True
        else:
            # "processing" também: uma cópia que ficou pelo caminho é refeita
            # (a cópia é idempotente).
            outdated = record.status != "ready" or record.content_sha256 != document.content_sha256
        if outdated:
            record.size_bytes = document.size_bytes
            record.mime_type = document.mime_type
            record.category = document.category
            record.content_sha256 = document.content_sha256
            record.pages_total = document.pages_total
            record.partial_pages = document.partial_pages
            record.status = "processing"
            record.error_message = None
            to_copy.append(record.id)
        # Flush antes do vínculo: a FK exige a linha do arquivo já gravada.
        await session.flush()
        if record.id not in linked:
            session.add(AgentKnowledgeBaseFile(agent_id=agent.id, knowledge_base_file_id=record.id))
            linked.add(record.id)
    await session.flush()
    return to_copy


async def detach_agent_package(session: AsyncSession, agent: Agent) -> None:
    """Tira do agente as cópias de pacote (troca ou remoção da especialidade) e
    exclui as que não ficaram ligadas a nenhum outro agente. Não faz commit."""
    package_files = (
        await session.scalars(
            select(KnowledgeBaseFile)
            .join(
                AgentKnowledgeBaseFile,
                AgentKnowledgeBaseFile.knowledge_base_file_id == KnowledgeBaseFile.id,
            )
            .where(
                AgentKnowledgeBaseFile.agent_id == agent.id,
                KnowledgeBaseFile.tenant_id == agent.tenant_id,
                KnowledgeBaseFile.public_document_id.is_not(None),
            )
        )
    ).all()
    if not package_files:
        return
    ids = [record.id for record in package_files]
    await session.execute(
        delete(AgentKnowledgeBaseFile).where(
            AgentKnowledgeBaseFile.agent_id == agent.id,
            AgentKnowledgeBaseFile.knowledge_base_file_id.in_(ids),
        )
    )
    still_linked = set(
        (
            await session.scalars(
                select(AgentKnowledgeBaseFile.knowledge_base_file_id).where(
                    AgentKnowledgeBaseFile.knowledge_base_file_id.in_(ids)
                )
            )
        ).all()
    )
    orphans = [record for record in package_files if record.id not in still_linked]
    if not orphans:
        return
    try:
        await delete_documents(str(agent.tenant_id), [str(record.id) for record in orphans])
    except RagApiError as exc:
        # O registro some do painel de qualquer forma; os trechos órfãos não são
        # alcançados por nenhuma busca (o agente só busca nos ids ligados a ele).
        logger.warning(
            "Falha ao remover cópias de pacote no api_rag | tenant=%s error_type=%s",
            agent.tenant_id,
            safe_error(exc),
        )
    for record in orphans:
        await session.delete(record)


async def enqueue_copies(arq: ArqRedis, tenant_id: uuid.UUID, file_ids: list[uuid.UUID]) -> None:
    """Enfileira a cópia do conteúdo indexado — sempre depois do commit."""
    for file_id in file_ids:
        await arq.enqueue_job(
            "copy_public_document",
            tenant_id=str(tenant_id),
            file_id=str(file_id),
            _queue_name=INGESTION_QUEUE,
        )
