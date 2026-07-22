"""Painel de conversas: listagem, histórico, takeover, resposta humana e resumo sob demanda."""

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
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
    EndCustomerBalance,
    EndCustomerCreditTransaction,
    Message,
    Tenant,
    TenantBillingSettings,
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
    conversations = result.scalars().all()
    phone_numbers = [c.contact_phone_number for c in conversations]
    balances = await _end_customer_balances_by_phone(session, ctx.tenant_id, phone_numbers)
    cycles = await _end_customer_cycles_by_phone(session, ctx.tenant_id, phone_numbers)
    return [
        _to_conversation_out(
            c, balances.get(c.contact_phone_number), cycles.get(c.contact_phone_number)
        )
        for c in conversations
    ]


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
    balances = await _end_customer_balances_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    cycles = await _end_customer_cycles_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    return _to_conversation_out(
        conversation,
        balances.get(conversation.contact_phone_number),
        cycles.get(conversation.contact_phone_number),
    )


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
    balances = await _end_customer_balances_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    cycles = await _end_customer_cycles_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    return _to_conversation_out(
        conversation,
        balances.get(conversation.contact_phone_number),
        cycles.get(conversation.contact_phone_number),
    )


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


async def _end_customer_balances_by_phone(
    session: AsyncSession, tenant_id: uuid.UUID, phone_numbers: list[str]
) -> dict[str, Decimal]:
    """Saldo do cliente final por contato — só populado quando a cobrança do
    cliente final está habilitada pro tenant; caso contrário (ou sem contatos
    pra buscar) retorna {} e o campo do conversation fica None."""
    if not phone_numbers:
        return {}
    result = await session.execute(
        select(EndCustomerBalance.contact_phone_number, EndCustomerBalance.credit_balance)
        .join(
            TenantBillingSettings,
            TenantBillingSettings.tenant_id == EndCustomerBalance.tenant_id,
        )
        .where(
            TenantBillingSettings.enabled.is_(True),
            EndCustomerBalance.tenant_id == tenant_id,
            EndCustomerBalance.contact_phone_number.in_(phone_numbers),
        )
    )
    return {row.contact_phone_number: row.credit_balance for row in result.all()}


async def _end_customer_cycles_by_phone(
    session: AsyncSession, tenant_id: uuid.UUID, phone_numbers: list[str]
) -> dict[str, tuple[Decimal, Decimal]]:
    """Ciclo de créditos atual por contato: total da compra mais recente e
    quanto já foi consumido desde ela — reseta a cada nova compra (não é o
    total/consumo vitalício, que fica na aba "Clientes" de
    /configuracoes/cobranca-clientes). Só populado quando a cobrança do
    cliente final está habilitada pro tenant (mesmo gate de
    _end_customer_balances_by_phone); contato sem nenhuma compra não
    aparece no dict retornado."""
    if not phone_numbers:
        return {}

    purchases_result = await session.execute(
        select(
            EndCustomerCreditTransaction.contact_phone_number,
            EndCustomerCreditTransaction.amount_credits,
            EndCustomerCreditTransaction.created_at,
        )
        .join(
            TenantBillingSettings,
            TenantBillingSettings.tenant_id == EndCustomerCreditTransaction.tenant_id,
        )
        .where(
            TenantBillingSettings.enabled.is_(True),
            EndCustomerCreditTransaction.tenant_id == tenant_id,
            EndCustomerCreditTransaction.contact_phone_number.in_(phone_numbers),
            EndCustomerCreditTransaction.type == "purchase",
        )
    )
    latest_purchase: dict[str, tuple[Decimal, datetime]] = {}
    for row in purchases_result.all():
        current = latest_purchase.get(row.contact_phone_number)
        if current is None or row.created_at > current[1]:
            latest_purchase[row.contact_phone_number] = (row.amount_credits, row.created_at)

    if not latest_purchase:
        return {}

    consumption_result = await session.execute(
        select(
            EndCustomerCreditTransaction.contact_phone_number,
            EndCustomerCreditTransaction.amount_credits,
            EndCustomerCreditTransaction.created_at,
        ).where(
            EndCustomerCreditTransaction.tenant_id == tenant_id,
            EndCustomerCreditTransaction.contact_phone_number.in_(latest_purchase.keys()),
            EndCustomerCreditTransaction.type == "consumption",
        )
    )
    consumption_rows = consumption_result.all()

    cycles: dict[str, tuple[Decimal, Decimal]] = {}
    for phone, (total, purchased_at) in latest_purchase.items():
        consumed = sum(
            (
                -row.amount_credits
                for row in consumption_rows
                if row.contact_phone_number == phone and row.created_at > purchased_at
            ),
            start=Decimal(0),
        )
        cycles[phone] = (total, consumed)
    return cycles


def _to_conversation_out(
    conversation: Conversation,
    end_customer_balance: Decimal | None,
    end_customer_cycle: tuple[Decimal, Decimal] | None = None,
) -> ConversationOut:
    out = ConversationOut.model_validate(conversation)
    out.end_customer_balance = (
        float(end_customer_balance) if end_customer_balance is not None else None
    )
    if end_customer_cycle is not None:
        out.end_customer_cycle_total = float(end_customer_cycle[0])
        out.end_customer_cycle_consumed = float(end_customer_cycle[1])
    return out


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
