"""Draft changes are serialized on the agent row; only publish updates live fields."""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, AgentTestSession
from app.schemas.agents import AgentOut, AgentWorkspaceOut


async def get_agent(session: AsyncSession, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
    agent = await session.scalar(
        select(Agent)
        .where(Agent.id == agent_id, Agent.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if agent is None:
        raise HTTPException(404, "Agente não encontrado")
    return agent


def check_revision(agent: Agent, expected_revision: int) -> None:
    if agent.draft_revision != expected_revision:
        raise HTTPException(
            409,
            "A configuração foi alterada em outra sessão. "
            "Copie seu texto e recarregue antes de continuar.",
        )


def workspace(agent: Agent) -> AgentWorkspaceOut:
    name = agent.draft_name if agent.draft_name is not None else agent.name
    instructions = (
        agent.draft_instructions if agent.draft_instructions is not None else agent.instructions
    )
    return AgentWorkspaceOut(
        published=AgentOut.model_validate(agent),
        published_version=agent.published_version,
        draft_revision=agent.draft_revision,
        name=name,
        instructions=instructions,
        has_unpublished_changes=(name, instructions) != (agent.name, agent.instructions),
    )


async def test_snapshot(
    session: AsyncSession, tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> AgentTestSession | None:
    snapshot = await session.scalar(
        select(AgentTestSession).where(
            AgentTestSession.conversation_id == conversation_id,
            AgentTestSession.tenant_id == tenant_id,
        )
    )
    if snapshot is None:
        return None
    revision = await session.scalar(
        select(Agent.draft_revision).where(
            Agent.id == snapshot.agent_id, Agent.tenant_id == tenant_id
        )
    )
    if revision != snapshot.draft_revision:
        raise HTTPException(
            409, "O rascunho mudou. Inicie um novo teste para usar a configuração atual."
        )
    return snapshot
