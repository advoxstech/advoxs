"""Conversas de teste: o tenant conversa com os próprios agentes sem WhatsApp.

Diferente do playground do admin (efêmero), aqui tudo persiste em
conversations/messages e o consumo debita créditos normalmente — teste gasta
token real de LLM.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.agents import send_playground_message
from app.models import Conversation, CreditTransaction, Message, Tenant
from app.services.pricing import calcular_creditos, get_current_pricing_config


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
    tokens_input = result.get("tokens_input", 0)
    tokens_output = result.get("tokens_output", 0)
    config = await get_current_pricing_config(session)
    credits = calcular_creditos(tokens_input, tokens_output, tokens_used, config)

    now = datetime.now(UTC)
    agent_messages: list[Message] = []
    for i, text in enumerate(responses):
        message = Message(
            conversation_id=conversation.id,
            tenant_id=tenant_id,
            sender_type="agent",
            content=text,
            # Mesma execução pode gerar várias respostas (ex: despedida da
            # secretária + saudação do especialista) — sem offset por índice,
            # todas cravam o mesmo instante e a ordenação por created_at não
            # tem como desempatar a ordem real de geração.
            created_at=now + timedelta(microseconds=i),
            tokens_used=tokens_used if i == 0 else None,
            credits_consumed=credits if i == 0 else None,
        )
        session.add(message)
        agent_messages.append(message)
    conversation.last_message_at = now
    await session.flush()

    if credits and agent_messages:
        # Ledger + saldo na mesma transação das mensagens (fórmula do worker).
        # Lock da linha do tenant: serializa débitos concorrentes do saldo.
        await session.execute(
            select(Tenant.credit_balance).where(Tenant.id == tenant_id).with_for_update()
        )
        session.add(
            CreditTransaction(
                tenant_id=tenant_id,
                type="consumption",
                amount_credits=-credits,
                related_message_id=agent_messages[0].id,
                tokens_input=tokens_input or None,
                tokens_output=tokens_output or None,
                pricing_config_id=config.id,
                description="Consumo do agente em conversa de teste",
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
