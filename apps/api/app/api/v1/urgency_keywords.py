"""Palavras-chave de detecção direta de urgência, editáveis por agente."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.models import Agent, Tenant, UrgencyKeyword
from app.services.urgency import (
    DEFAULT_URGENCY_KEYWORDS,
    MAX_KEYWORDS_PER_AGENT,
    list_keywords,
    normalize,
    restore_default_keywords,
)

router = APIRouter(prefix="/agents/{agent_id}/urgency-keywords", tags=["urgency"])


class UrgencyKeywordIn(BaseModel):
    keyword: str = Field(min_length=1, max_length=60)

    @field_validator("keyword")
    @classmethod
    def strip(cls, value: str) -> str:
        value = " ".join(value.split())
        if not normalize(value):
            raise ValueError("Informe uma palavra ou frase.")
        return value


class UrgencyKeywordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    keyword: str
    created_at: datetime


async def _lock_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    # Serializa inclusões concorrentes pra contagem do limite ficar correta.
    await session.execute(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())


async def _require_agent(session: AsyncSession, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> None:
    exists = await session.scalar(
        select(Agent.id).where(Agent.id == agent_id, Agent.tenant_id == tenant_id)
    )
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agente não encontrado.")


@router.get("")
async def get_keywords(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[UrgencyKeywordOut]:
    await _require_agent(session, ctx.tenant_id, agent_id)
    return [
        UrgencyKeywordOut.model_validate(k)
        for k in await list_keywords(session, ctx.tenant_id, agent_id)
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_keyword(
    agent_id: uuid.UUID,
    body: UrgencyKeywordIn,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> UrgencyKeywordOut:
    await _require_agent(session, ctx.tenant_id, agent_id)
    await _lock_tenant(session, ctx.tenant_id)
    count = await session.scalar(
        select(func.count(UrgencyKeyword.id)).where(
            UrgencyKeyword.tenant_id == ctx.tenant_id,
            UrgencyKeyword.agent_id == agent_id,
        )
    )
    if (count or 0) >= MAX_KEYWORDS_PER_AGENT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Limite de {MAX_KEYWORDS_PER_AGENT} palavras-chave atingido.",
        )
    keyword = UrgencyKeyword(
        tenant_id=ctx.tenant_id,
        agent_id=agent_id,
        keyword=body.keyword,
        normalized=normalize(body.keyword),
    )
    session.add(keyword)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Essa palavra-chave já está na lista.")
    await session.refresh(keyword)
    return UrgencyKeywordOut.model_validate(keyword)


@router.post("/restore-defaults")
async def restore_defaults(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[UrgencyKeywordOut]:
    await _require_agent(session, ctx.tenant_id, agent_id)
    await _lock_tenant(session, ctx.tenant_id)
    existing = set(
        await session.scalars(
            select(UrgencyKeyword.normalized).where(
                UrgencyKeyword.tenant_id == ctx.tenant_id,
                UrgencyKeyword.agent_id == agent_id,
            )
        )
    )
    missing_defaults = {normalize(keyword) for keyword in DEFAULT_URGENCY_KEYWORDS} - existing
    if len(existing) + len(missing_defaults) > MAX_KEYWORDS_PER_AGENT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Remova algumas palavras antes de restaurar a lista padrão.",
        )
    await restore_default_keywords(session, ctx.tenant_id, agent_id)
    await session.commit()
    return [
        UrgencyKeywordOut.model_validate(k)
        for k in await list_keywords(session, ctx.tenant_id, agent_id)
    ]


@router.delete("/{keyword_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_keyword(
    agent_id: uuid.UUID,
    keyword_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    await _require_agent(session, ctx.tenant_id, agent_id)
    result = await session.execute(
        delete(UrgencyKeyword).where(
            UrgencyKeyword.id == keyword_id,
            UrgencyKeyword.tenant_id == ctx.tenant_id,
            UrgencyKeyword.agent_id == agent_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Palavra-chave não encontrada.")
    await session.commit()
