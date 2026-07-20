"""CRUD de agentes de IA próprios do tenant."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.models import Agent, AgentKnowledgeBaseFile, KnowledgeBaseFile
from app.schemas.agents import (
    AgentCreate,
    AgentKnowledgeBaseFileOut,
    AgentOut,
    AgentUpdate,
    AttachKnowledgeBaseFileIn,
)

router = APIRouter(prefix="/agents", tags=["agents"])


async def _unset_current_entry_point(ctx: TenantContext, session: AsyncSession) -> None:
    await session.execute(
        update(Agent)
        .where(Agent.tenant_id == ctx.tenant_id, Agent.is_entry_point.is_(True))
        .values(is_entry_point=False)
    )


@router.get("")
async def list_agents(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[AgentOut]:
    result = await session.execute(
        select(Agent).where(Agent.tenant_id == ctx.tenant_id).order_by(Agent.created_at)
    )
    return [AgentOut.model_validate(a) for a in result.scalars().all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentOut:
    if body.is_entry_point:
        await _unset_current_entry_point(ctx, session)

    agent = Agent(id=uuid.uuid4(), tenant_id=ctx.tenant_id, **body.model_dump())
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return AgentOut.model_validate(agent)


async def _get_agent(agent_id: uuid.UUID, ctx: TenantContext, session: AsyncSession) -> Agent:
    agent = await session.scalar(
        select(Agent).where(Agent.id == agent_id, Agent.tenant_id == ctx.tenant_id)
    )
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente não encontrado")
    return agent


@router.patch("/{agent_id}")
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentOut:
    agent = await _get_agent(agent_id, ctx, session)

    if body.is_entry_point is False and agent.is_entry_point:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Não é possível desmarcar o único ponto de entrada — "
                "marque outro agente como ponto de entrada antes"
            ),
        )

    if body.is_entry_point is True and not agent.is_entry_point:
        await _unset_current_entry_point(ctx, session)

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(agent, field, value)

    await session.commit()
    await session.refresh(agent)
    return AgentOut.model_validate(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    agent = await _get_agent(agent_id, ctx, session)

    if agent.is_entry_point:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Não é possível apagar o agente ponto de entrada — "
                "marque outro agente como ponto de entrada antes"
            ),
        )

    total = await session.scalar(
        select(func.count()).select_from(Agent).where(Agent.tenant_id == ctx.tenant_id)
    )
    if total <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="O tenant precisa ter ao menos 1 agente — crie outro antes de apagar este",
        )

    await session.delete(agent)
    await session.commit()


@router.post("/{agent_id}/knowledge-base-files", status_code=status.HTTP_201_CREATED)
async def attach_knowledge_base_file(
    agent_id: uuid.UUID,
    body: AttachKnowledgeBaseFileIn,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentKnowledgeBaseFileOut:
    await _get_agent(agent_id, ctx, session)

    file = await session.scalar(
        select(KnowledgeBaseFile).where(
            KnowledgeBaseFile.id == body.knowledge_base_file_id,
            KnowledgeBaseFile.tenant_id == ctx.tenant_id,
        )
    )
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Arquivo não encontrado"
        )

    link = AgentKnowledgeBaseFile(agent_id=agent_id, knowledge_base_file_id=file.id)
    session.add(link)
    await session.commit()
    return AgentKnowledgeBaseFileOut.model_validate(link)


@router.delete(
    "/{agent_id}/knowledge-base-files/{file_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def detach_knowledge_base_file(
    agent_id: uuid.UUID,
    file_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    await _get_agent(agent_id, ctx, session)

    link = await session.get(AgentKnowledgeBaseFile, (agent_id, file_id))
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vínculo não encontrado")

    await session.delete(link)
    await session.commit()
