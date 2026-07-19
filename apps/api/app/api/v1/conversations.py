"""Painel de conversas: listagem, histórico, takeover, resposta humana e resumo sob demanda."""

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.agents import (
    AgentsApiError,
    AgentsNetworkError,
    delete_agent_checkpoint,
    generate_conversation_summary,
    sync_conversation_context,
)
from app.clients.whatsapp import WhatsAppSendError, send_text_message
from app.core.crypto import decrypt_access_token
from app.models import (
    Conversation,
    CreditTransaction,
    EndCustomerCreditTransaction,
    Message,
    Tenant,
    WhatsAppNumber,
)
from app.schemas.conversations import (
    ConversationOut,
    ConversationStateUpdate,
    ConversationUsageOut,
    MessageOut,
    SendMessageRequest,
)
from app.services.conversations_usage import build_conversations_usage
from app.services.pricing import calcular_creditos, get_current_pricing_config

router = APIRouter(prefix="/conversations", tags=["conversations"])
logger = logging.getLogger(__name__)


@router.get("")
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    origin: Literal["real", "test"] = Query(default="real"),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ConversationOut]:
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.tenant_id == ctx.tenant_id,
            Conversation.is_test == (origin == "test"),
        )
        .order_by(Conversation.last_message_at.desc().nulls_last())
        .limit(limit)
        .offset(offset)
    )
    return [ConversationOut.model_validate(c) for c in result.scalars().all()]


@router.get("/usage")
async def get_conversations_usage(
    from_: date = Query(..., alias="from"),
    to: date = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ConversationUsageOut]:
    if to < from_:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'to' não pode ser anterior a 'from'",
        )
    return await build_conversations_usage(session, ctx.tenant_id, from_, to, limit, offset)


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
    if body.state == "human":
        # Takeover começa "presente" — o heartbeat do painel mantém depois.
        conversation.human_last_seen_at = datetime.now(UTC)
    await session.commit()
    return ConversationOut.model_validate(conversation)


@router.post("/{conversation_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def heartbeat(
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Presença do atendente: o painel envia a cada ciclo de polling enquanto
    a conversa está aberta em modo human. O worker usa human_last_seen_at pra
    decidir se a IA reassume (timeout)."""
    conversation = await _get_conversation(conversation_id, ctx, session)
    conversation.human_last_seen_at = datetime.now(UTC)
    await session.commit()


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
        delivery_status="sent",
    )
    session.add(message)
    conversation.last_message_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(message)

    try:
        await sync_conversation_context(
            tenant_id=str(ctx.tenant_id),
            contact_phone_number=conversation.contact_phone_number,
            role="attendant",
            content=body.content,
        )
    except (AgentsNetworkError, AgentsApiError) as exc:
        # Best-effort: a mensagem já foi entregue ao contato — sem o sync o
        # agente fica com um buraco de memória, mas a operação não falha.
        logger.warning(
            "Falha ao sincronizar contexto do takeover | conversation=%s erro=%s",
            conversation_id,
            exc,
        )

    return MessageOut.model_validate(message)


@router.post("/{conversation_id}/summary")
async def generate_summary(
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ConversationOut:
    """Resumo sob demanda via LLM (agents service) — consome créditos do tenant."""
    conversation = await _get_conversation(conversation_id, ctx, session)

    tenant = await session.get(Tenant, ctx.tenant_id)
    if tenant.credit_balance <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Saldo de créditos esgotado — não é possível gerar o resumo",
        )

    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    history = result.scalars().all()
    if not history:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversa sem mensagens — nada para resumir",
        )

    try:
        summary_result = await generate_conversation_summary(
            [{"sender_type": m.sender_type, "content": m.content} for m in history]
        )
    except (AgentsNetworkError, AgentsApiError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    tokens_used = summary_result["tokens_used"]
    config = await get_current_pricing_config(session)
    credits = calcular_creditos(
        summary_result.get("tokens_input", 0),
        summary_result.get("tokens_output", 0),
        tokens_used,
        config,
    )

    conversation.summary = summary_result["summary"]
    conversation.summary_generated_at = datetime.now(UTC)

    if credits:
        # Lock da linha do tenant: serializa débitos concorrentes do saldo.
        await session.execute(
            select(Tenant.credit_balance).where(Tenant.id == ctx.tenant_id).with_for_update()
        )
        session.add(
            CreditTransaction(
                tenant_id=ctx.tenant_id,
                type="consumption",
                amount_credits=-credits,
                related_message_id=None,
                tokens_input=summary_result.get("tokens_input") or None,
                tokens_output=summary_result.get("tokens_output") or None,
                pricing_config_id=config.id,
                description="Resumo de conversa gerado",
            )
        )
        await session.execute(
            update(Tenant)
            .where(Tenant.id == ctx.tenant_id)
            .values(credit_balance=Tenant.credit_balance - credits)
        )

    await session.commit()
    return ConversationOut.model_validate(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Apaga mensagens + conversa (real ou de teste); ledger fica (related_message_id
    vira NULL nas duas tabelas — tenant e cliente final —, o consumo continua
    auditável). Checkpoint no agents é limpado best-effort. Irreversível."""
    conversation = await _get_conversation(conversation_id, ctx, session)
    thread_id = f"{ctx.tenant_id}:{conversation.contact_phone_number}"
    logger.info(
        "Excluindo histórico de conversa | tenant_id=%s conversation_id=%s contact=%s",
        ctx.tenant_id,
        conversation.id,
        conversation.contact_phone_number,
    )

    message_ids = select(Message.id).where(Message.conversation_id == conversation.id)
    await session.execute(
        update(CreditTransaction)
        .where(CreditTransaction.related_message_id.in_(message_ids))
        .values(related_message_id=None)
    )
    await session.execute(
        update(EndCustomerCreditTransaction)
        .where(EndCustomerCreditTransaction.related_message_id.in_(message_ids))
        .values(related_message_id=None)
    )
    await session.execute(sql_delete(Message).where(Message.conversation_id == conversation.id))
    await session.delete(conversation)
    await session.commit()

    await delete_agent_checkpoint(thread_id)


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
