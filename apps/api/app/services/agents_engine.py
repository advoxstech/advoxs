"""Carrega a lista de agentes de um tenant no formato que o agents service
espera receber em POST /messages — usado pelo playground de admin e pelas
conversas de teste (mensagens reais de WhatsApp usam o equivalente no
worker, ver apps/worker/app/tasks/messages.py). O agents service nunca
acessa este Postgres diretamente."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, AgentKnowledgeBaseFile


async def load_agents_for_engine(session: AsyncSession, tenant_id: uuid.UUID) -> list[dict]:
    agents_result = await session.execute(select(Agent).where(Agent.tenant_id == tenant_id))
    agents = agents_result.scalars().all()

    links_result = await session.execute(
        select(AgentKnowledgeBaseFile.agent_id, AgentKnowledgeBaseFile.knowledge_base_file_id)
        .join(Agent, Agent.id == AgentKnowledgeBaseFile.agent_id)
        .where(Agent.tenant_id == tenant_id)
    )
    kb_by_agent: dict[uuid.UUID, list[str]] = {}
    for agent_id, file_id in links_result.all():
        kb_by_agent.setdefault(agent_id, []).append(str(file_id))

    return [
        {
            "id": str(agent.id),
            "name": agent.name,
            "instructions": agent.instructions,
            "is_entry_point": agent.is_entry_point,
            "knowledge_base_file_ids": kb_by_agent.get(agent.id, []),
        }
        for agent in agents
    ]
