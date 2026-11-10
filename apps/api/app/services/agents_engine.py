"""Carrega a lista de agentes de um tenant no formato que o agents service
espera receber em POST /messages — usado pelo playground de admin e pelas
conversas de teste (mensagens reais de WhatsApp usam o equivalente no
worker, ver apps/worker/app/tasks/messages.py). O agents service nunca
acessa este Postgres diretamente."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.knowledge_tree import UNREADABLE_CATEGORY
from app.models import Agent, AgentKnowledgeBaseFile, KnowledgeBaseFile


async def load_agents_for_engine(session: AsyncSession, tenant_id: uuid.UUID) -> list[dict]:
    agents_result = await session.execute(select(Agent).where(Agent.tenant_id == tenant_id))
    agents = agents_result.scalars().all()

    links_result = await session.execute(
        select(
            AgentKnowledgeBaseFile.agent_id,
            AgentKnowledgeBaseFile.knowledge_base_file_id,
            KnowledgeBaseFile.public_document_id,
        )
        .join(Agent, Agent.id == AgentKnowledgeBaseFile.agent_id)
        .join(
            KnowledgeBaseFile,
            KnowledgeBaseFile.id == AgentKnowledgeBaseFile.knowledge_base_file_id,
        )
        .where(
            Agent.tenant_id == tenant_id,
            # Pasta F ("não selecionáveis"): o agente não consulta.
            KnowledgeBaseFile.category.is_distinct_from(UNREADABLE_CATEGORY),
        )
    )
    kb_by_agent: dict[uuid.UUID, list[str]] = {}
    # Cópias do pacote de especialidade (legislação e jurisprudência): buscadas
    # à parte, para não disputar espaço com os documentos do próprio escritório.
    reference_by_agent: dict[uuid.UUID, list[str]] = {}
    for agent_id, file_id, public_document_id in links_result.all():
        kb_by_agent.setdefault(agent_id, []).append(str(file_id))
        if public_document_id is not None:
            reference_by_agent.setdefault(agent_id, []).append(str(file_id))

    return [
        {
            "id": str(agent.id),
            "name": agent.name,
            "instructions": agent.instructions,
            "is_entry_point": agent.is_entry_point,
            "knowledge_base_file_ids": kb_by_agent.get(agent.id, []),
            # Só quando há cópias de pacote (subconjunto da lista acima).
            **_reference(reference_by_agent.get(agent.id)),
        }
        for agent in agents
    ]


def _reference(file_ids: list[str] | None) -> dict:
    return {"reference_file_ids": file_ids} if file_ids else {}
