"""Conversas de teste — aba Testes do painel do tenant (sem WhatsApp)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.agents import AgentsApiError, AgentsNetworkError
from app.models import Conversation, Tenant
from app.schemas.conversations import (
    ConversationOut,
    MessageOut,
    SendMessageRequest,
    TestMessagesOut,
)
from app.services import test_conversations as service

router = APIRouter(tags=["test-conversations"])

_AGENTS_ERROR_DETAIL = "Não foi possível falar com o agente agora — tente novamente"


@router.post("/test-conversations", status_code=status.HTTP_201_CREATED)
async def create_test_conversation(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ConversationOut:
    conversation = Conversation(
        tenant_id=ctx.tenant_id,
        contact_phone_number=f"teste-{uuid.uuid4().hex[:12]}",
        is_test=True,
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.post(
    "/conversations/{conversation_id}/test-messages",
    status_code=status.HTTP_201_CREATED,
)
async def send_test_message(
    conversation_id: uuid.UUID,
    body: SendMessageRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> TestMessagesOut:
    conversation = await _get_test_conversation(conversation_id, ctx, session)

    tenant = await session.get(Tenant, ctx.tenant_id)
    if tenant.credit_balance <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Saldo de créditos esgotado — não é possível testar o agente",
        )

    try:
        messages, grouped = await service.send_test_message(
            session, ctx.tenant_id, conversation, body.content
        )
    except (AgentsNetworkError, AgentsApiError):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_AGENTS_ERROR_DETAIL)

    return TestMessagesOut(
        messages=[MessageOut.model_validate(m) for m in messages], grouped=grouped
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_test_conversation(
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    conversation = await _get_test_conversation(conversation_id, ctx, session)
    await service.delete_test_conversation(session, ctx.tenant_id, conversation)


async def _get_test_conversation(
    conversation_id: uuid.UUID, ctx: TenantContext, session: AsyncSession
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == ctx.tenant_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversa não encontrada")
    if not conversation.is_test:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Operação disponível apenas para conversas de teste",
        )
    return conversation
