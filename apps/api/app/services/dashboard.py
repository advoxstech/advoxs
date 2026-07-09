"""Snapshot agregado do painel do escritório — todas as queries filtradas
pelo tenant autenticado (defesa em profundidade junto com o RLS da sessão)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Conversation,
    CreditTransaction,
    KnowledgeBaseFile,
    Message,
    Tenant,
    WhatsAppNumber,
)
from app.schemas.dashboard import (
    ConversationsSummaryOut,
    KnowledgeBaseSummaryOut,
    RecentConversationOut,
    TenantDashboardOut,
    UsageSummaryOut,
    WhatsappStatusOut,
)

PERIOD_DAYS = 30
RECENT_LIMIT = 5


def _mask_phone_number(value: str) -> str:
    """Mesmo formato de GET /whatsapp/connection: DDI + 4 últimos dígitos."""
    if len(value) <= 7:
        return value
    return f"{value[:3]} **** {value[-4:]}"


async def build_tenant_dashboard(session: AsyncSession, tenant_id: uuid.UUID) -> TenantDashboardOut:
    since = datetime.now(UTC) - timedelta(days=PERIOD_DAYS)

    credit_balance = (
        await session.scalar(select(Tenant.credit_balance).where(Tenant.id == tenant_id))
    ) or 0

    display_phone_number = await session.scalar(
        select(WhatsAppNumber.display_phone_number).where(
            WhatsAppNumber.tenant_id == tenant_id, WhatsAppNumber.status == "connected"
        )
    )

    conversations_total = (
        await session.scalar(
            select(func.count(Conversation.id)).where(Conversation.tenant_id == tenant_id)
        )
    ) or 0
    waiting_human = (
        await session.scalar(
            select(func.count(Conversation.id)).where(
                Conversation.tenant_id == tenant_id, Conversation.state == "human"
            )
        )
    ) or 0

    agent_messages = (
        await session.scalar(
            select(func.count(Message.id)).where(
                Message.tenant_id == tenant_id,
                Message.sender_type == "agent",
                Message.created_at >= since,
            )
        )
    ) or 0
    credits_consumed_negative = (
        await session.scalar(
            select(func.coalesce(func.sum(CreditTransaction.amount_credits), 0)).where(
                CreditTransaction.tenant_id == tenant_id,
                CreditTransaction.type == "consumption",
                CreditTransaction.created_at >= since,
            )
        )
    ) or 0

    kb_ready = (
        await session.scalar(
            select(func.count(KnowledgeBaseFile.id)).where(
                KnowledgeBaseFile.tenant_id == tenant_id, KnowledgeBaseFile.status == "ready"
            )
        )
    ) or 0
    kb_error = (
        await session.scalar(
            select(func.count(KnowledgeBaseFile.id)).where(
                KnowledgeBaseFile.tenant_id == tenant_id, KnowledgeBaseFile.status == "error"
            )
        )
    ) or 0

    recent = (
        (
            await session.execute(
                select(Conversation)
                .where(Conversation.tenant_id == tenant_id)
                .order_by(Conversation.last_message_at.desc().nulls_last())
                .limit(RECENT_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    return TenantDashboardOut(
        credit_balance=credit_balance,
        whatsapp=WhatsappStatusOut(
            connected=display_phone_number is not None,
            display_phone_number=(
                _mask_phone_number(display_phone_number) if display_phone_number else None
            ),
        ),
        conversations=ConversationsSummaryOut(
            total=conversations_total, waiting_human=waiting_human
        ),
        usage_last_30_days=UsageSummaryOut(
            agent_messages=agent_messages, credits_consumed=abs(credits_consumed_negative)
        ),
        knowledge_base=KnowledgeBaseSummaryOut(ready=kb_ready, error=kb_error),
        recent_conversations=[RecentConversationOut.model_validate(c) for c in recent],
    )
