"""Conversas de teste: o tenant conversa com os próprios agentes sem WhatsApp.

Diferente do playground do admin (efêmero), aqui tudo persiste em
conversations/messages e o consumo debita créditos normalmente — teste gasta
token real de LLM.
"""

import math
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete as sql_delete
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.agents import delete_playground_conversation, send_playground_message
from app.core.config import settings
from app.models import Conversation, CreditTransaction, Message, Tenant


async def send_test_message(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    conversation: Conversation,
    content: str,
) -> tuple[list[Message], bool]:
    """Persiste a mensagem do usuário (como contato), roda o agente síncrono e
    persiste/debita as respostas. Retorna (mensagens novas, grouped)."""
    now = datetime.now(UTC)
    contact_message = Message(
        conversation_id=conversation.id,
        tenant_id=tenant_id,
        sender_type="contact",
        content=content,
        created_at=now,
    )
    session.add(contact_message)
    conversation.last_message_at = now
    # Commit ANTES da chamada ao agents: se ele falhar, a mensagem do usuário
    # sobrevive no histórico (mesma filosofia do fluxo real via webhook).
    await session.commit()
    await session.refresh(contact_message)

    result = await send_playground_message(
        tenant_id=str(tenant_id),
        contact_phone_number=conversation.contact_phone_number,
        message=content,
    )
    if result is None:
        # 202: debounce agrupou numa execução em andamento — as respostas
        # serão persistidas pela requisição que está rodando.
        return [contact_message], True

    responses: list[str] = result["responses"]
    tokens_used = result["tokens_used"] or 0
    credits = math.ceil(tokens_used / settings.credit_tokens_per_credit) if tokens_used else 0

    now = datetime.now(UTC)
    agent_messages: list[Message] = []
    for i, text in enumerate(responses):
        message = Message(
            conversation_id=conversation.id,
            tenant_id=tenant_id,
            sender_type="agent",
            content=text,
            created_at=now,
            tokens_used=tokens_used if i == 0 else None,
            credits_consumed=credits if i == 0 else None,
        )
        session.add(message)
        agent_messages.append(message)
    conversation.last_message_at = now
    await session.flush()

    if credits and agent_messages:
        # Ledger + saldo na mesma transação das mensagens (fórmula do worker).
        session.add(
            CreditTransaction(
                tenant_id=tenant_id,
                type="consumption",
                amount_credits=-credits,
                related_message_id=agent_messages[0].id,
                description=f"Consumo do agente em conversa de teste ({tokens_used} tokens)",
            )
        )
        await session.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(credit_balance=Tenant.credit_balance - credits)
        )
    await session.commit()
    for message in agent_messages:
        await session.refresh(message)
    return [contact_message, *agent_messages], False


async def delete_test_conversation(
    session: AsyncSession, tenant_id: uuid.UUID, conversation: Conversation
) -> None:
    """Apaga mensagens + conversa; ledger fica (related_message_id vira NULL,
    o consumo continua auditável). Checkpoint no agents é limpado best-effort."""
    thread_id = f"{tenant_id}:{conversation.contact_phone_number}"

    message_ids = select(Message.id).where(Message.conversation_id == conversation.id)
    await session.execute(
        update(CreditTransaction)
        .where(CreditTransaction.related_message_id.in_(message_ids))
        .values(related_message_id=None)
    )
    await session.execute(sql_delete(Message).where(Message.conversation_id == conversation.id))
    await session.delete(conversation)
    await session.commit()

    # Best-effort (a função do client já loga e engole falhas internamente).
    await delete_playground_conversation(thread_id)
