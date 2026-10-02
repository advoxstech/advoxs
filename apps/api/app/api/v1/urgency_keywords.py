"""Palavras-chave de urgência editáveis pelo escritório."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.models import Tenant, UrgencyKeyword
from app.services.urgency import (
    MAX_KEYWORDS_PER_TENANT,
    list_keywords,
    normalize,
    restore_default_keywords,
)

router = APIRouter(prefix="/urgency-keywords", tags=["urgency"])


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


@router.get("")
async def get_keywords(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[UrgencyKeywordOut]:
    return [
        UrgencyKeywordOut.model_validate(k) for k in await list_keywords(session, ctx.tenant_id)
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_keyword(
    body: UrgencyKeywordIn,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> UrgencyKeywordOut:
    await _lock_tenant(session, ctx.tenant_id)
    count = await session.scalar(
        select(func.count(UrgencyKeyword.id)).where(UrgencyKeyword.tenant_id == ctx.tenant_id)
    )
    if (count or 0) >= MAX_KEYWORDS_PER_TENANT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Limite de {MAX_KEYWORDS_PER_TENANT} palavras-chave atingido.",
        )
    keyword = UrgencyKeyword(
        tenant_id=ctx.tenant_id, keyword=body.keyword, normalized=normalize(body.keyword)
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
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[UrgencyKeywordOut]:
    await _lock_tenant(session, ctx.tenant_id)
    await restore_default_keywords(session, ctx.tenant_id)
    await session.commit()
    return [
        UrgencyKeywordOut.model_validate(k) for k in await list_keywords(session, ctx.tenant_id)
    ]


@router.delete("/{keyword_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_keyword(
    keyword_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    result = await session.execute(
        delete(UrgencyKeyword).where(
            UrgencyKeyword.id == keyword_id, UrgencyKeyword.tenant_id == ctx.tenant_id
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Palavra-chave não encontrada.")
    await session.commit()
