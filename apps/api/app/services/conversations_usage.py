"""Agregação de consumo de créditos por conversa — relatório do tenant."""

import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message
from app.schemas.conversations import ConversationUsageOut


async def build_conversations_usage(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    date_from: date,
    date_to: date,
    limit: int,
    offset: int,
) -> list[ConversationUsageOut]:
    range_start = datetime.combine(date_from, time.min, tzinfo=UTC)
    range_end = datetime.combine(date_to, time.max, tzinfo=UTC)

    rows = (
        await session.execute(
            select(
                Message.conversation_id,
                Conversation.contact_phone_number,
                Conversation.is_test,
                func.sum(Message.credits_consumed),
                func.count(Message.id),
                func.max(Message.created_at),
            )
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.tenant_id == tenant_id,
                Message.credits_consumed.is_not(None),
                Message.created_at >= range_start,
                Message.created_at <= range_end,
            )
            .group_by(
                Message.conversation_id, Conversation.contact_phone_number, Conversation.is_test
            )
            .order_by(func.sum(Message.credits_consumed).desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    return [
        ConversationUsageOut(
            conversation_id=conversation_id,
            contact_phone_number=contact_phone_number,
            is_test=is_test,
            credits_consumed=credits_consumed,
            billed_responses=billed_responses,
            last_message_at=last_message_at,
        )
        for (
            conversation_id,
            contact_phone_number,
            is_test,
            credits_consumed,
            billed_responses,
            last_message_at,
        ) in rows
    ]
