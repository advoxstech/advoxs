"""Painel de conversas: listagem, histórico, takeover e resposta humana."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.whatsapp import WhatsAppSendError, send_text_message
from app.core.crypto import decrypt_access_token
from app.models import Conversation, Message, WhatsAppNumber
from app.schemas.conversations import (
    ConversationOut,
    ConversationStateUpdate,
    MessageOut,
    SendMessageRequest,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ConversationOut]:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.tenant_id == ctx.tenant_id)
        .order_by(Conversation.last_message_at.desc().nulls_last())
        .limit(limit)
        .offset(offset)
    )
    return [ConversationOut.model_validate(c) for c in result.scalars().all()]


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[MessageOut]:
    """Mensagens da conversa, da mais recente para a mais antiga."""
    await _get_conversation(conversation_id, ctx, session)

    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [MessageOut.model_validate(m) for m in result.scalars().all()]


@router.patch("/{conversation_id}")
async def update_state(
    conversation_id: uuid.UUID,
    body: ConversationStateUpdate,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ConversationOut:
    """Toggle de takeover: em modo `human`, o worker não aciona o agente."""
    conversation = await _get_conversation(conversation_id, ctx, session)
    conversation.state = body.state
    await session.commit()
    return ConversationOut.model_validate(conversation)


@router.post("/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_message(
    conversation_id: uuid.UUID,
    body: SendMessageRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> MessageOut:
    """Resposta manual do escritório (takeover) — envia via Graph API e persiste."""
    conversation = await _get_conversation(conversation_id, ctx, session)
    if conversation.state != "human":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversa em modo agente — assuma a conversa antes de responder",
        )

    number = await session.scalar(
        select(WhatsAppNumber).where(
            WhatsAppNumber.tenant_id == ctx.tenant_id,
            WhatsAppNumber.status == "connected",
        )
    )
    if number is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Escritório sem número de WhatsApp conectado",
        )

    try:
        await send_text_message(
            phone_number_id=number.phone_number_id,
            access_token=decrypt_access_token(number.access_token_encrypted),
            to=conversation.contact_phone_number,
            text=body.content,
        )
    except WhatsAppSendError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    message = Message(
        conversation_id=conversation.id,
        tenant_id=ctx.tenant_id,
        sender_type="human",
        content=body.content,
    )
    session.add(message)
    conversation.last_message_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(message)
    return MessageOut.model_validate(message)


async def _get_conversation(
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
    return conversation
