import logging
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from arq.worker import Retry
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import tables
from app.clients.agents import send_message_to_agents
from app.config import settings
from app.crypto import decrypt_access_token

logger = logging.getLogger(__name__)

# Na última tentativa, vira a conversa pra humano em vez de reagendar (o
# default de max_tries do Arq também é 5 — manter em sincronia, mesmo padrão
# já usado em apps/worker/app/tasks/knowledge_base.py).
MAX_TRIES = 5


@dataclass
class InboundContext:
    conversation_state: str
    contact_phone_number: str
    message_content: str
    phone_number_id: str
    access_token_encrypted: str
    credit_balance: int


async def process_inbound_message(
    ctx: dict, tenant_id: str, conversation_id: str, message_id: str
) -> None:
    """Verifica o estado da conversa (agent|human) e repassa para o agents service.

    Enfileirado pelo `api` depois de persistir a mensagem do contato. As respostas
    do agente voltam na chamada e são persistidas em `messages` (sender_type=agent).
    """
    session_factory = ctx["session_factory"]
    http: httpx.AsyncClient = ctx["http"]

    async with session_factory() as session:
        inbound = await _load_context(session, tenant_id, conversation_id, message_id)

    if inbound is None:
        return

    if inbound.conversation_state != "agent":
        # Takeover humano: a mensagem só aparece no painel de conversas.
        logger.info(
            "Conversa em modo humano, agente não acionado | tenant=%s conversation=%s",
            tenant_id,
            conversation_id,
        )
        return

    if inbound.credit_balance <= 0:
        # Saldo esgotado: silêncio total pro cliente final — a mensagem só
        # aparece no painel de conversas, aguardando um humano do escritório.
        logger.info(
            "Saldo esgotado, agente não acionado | tenant=%s conversation=%s saldo=%s",
            tenant_id,
            conversation_id,
            inbound.credit_balance,
        )
        return

    access_token = decrypt_access_token(inbound.access_token_encrypted)

    try:
        result = await send_message_to_agents(
            http,
            tenant_id=tenant_id,
            contact_phone_number=inbound.contact_phone_number,
            message=inbound.message_content,
            phone_number_id=inbound.phone_number_id,
            access_token=access_token,
        )
    except httpx.HTTPError as exc:
        if ctx.get("job_try", 1) < MAX_TRIES:
            # Erro transiente (rede, 5xx): reagenda com backoff crescente.
            logger.warning(
                "Falha ao chamar agents, reagendando | tenant=%s conversation=%s erro=%s",
                tenant_id,
                conversation_id,
                exc,
            )
            raise Retry(defer=ctx.get("job_try", 1) * 10)
        # Última tentativa: o agente não conseguiu processar. Diferente do
        # bloqueio por saldo esgotado (que só retorna em silêncio, sem mudar
        # o estado), aqui vira a conversa pra humano de propósito — alerta o
        # escritório, em vez de deixar o job desaparecer em silêncio depois
        # do TTL do resultado.
        logger.error(
            "Esgotadas as tentativas de chamar agents, virando conversa pra human | "
            "tenant=%s conversation=%s erro=%s",
            tenant_id,
            conversation_id,
            exc,
        )
        async with session_factory() as session:
            await session.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == uuid.UUID(conversation_id))
                .values(state="human")
            )
            await session.commit()
        return

    if result is None:
        # 202: debounce agrupou em execução já em andamento.
        logger.info(
            "Mensagem agrupada pelo debounce do agents | tenant=%s conversation=%s",
            tenant_id,
            conversation_id,
        )
        return

    responses = result["responses"]
    tokens_used = result.get("tokens_used", 0)
    delivery_failures = set(result.get("delivery_failures", []))
    # 1 crédito = N tokens, sempre arredondando pra cima — nunca cobra fração.
    credits = math.ceil(tokens_used / settings.credit_tokens_per_credit) if tokens_used else 0

    async with session_factory() as session:
        first_message_id = await _persist_agent_responses(
            session, tenant_id, conversation_id, responses, tokens_used, credits, delivery_failures
        )
        if credits and first_message_id is not None:
            # Ledger + saldo na mesma transação das mensagens.
            await _debitar_creditos(session, tenant_id, first_message_id, tokens_used, credits)
        await session.commit()


async def _load_context(
    session: AsyncSession, tenant_id: str, conversation_id: str, message_id: str
) -> InboundContext | None:
    conversation = (
        await session.execute(
            select(
                tables.conversations.c.state,
                tables.conversations.c.contact_phone_number,
            ).where(tables.conversations.c.id == uuid.UUID(conversation_id))
        )
    ).one_or_none()
    if conversation is None:
        logger.warning("Conversa não encontrada | conversation=%s", conversation_id)
        return None

    content = (
        await session.execute(
            select(tables.messages.c.content).where(tables.messages.c.id == uuid.UUID(message_id))
        )
    ).scalar_one_or_none()
    if content is None:
        logger.warning("Mensagem não encontrada | message=%s", message_id)
        return None

    number = (
        await session.execute(
            select(
                tables.whatsapp_numbers.c.phone_number_id,
                tables.whatsapp_numbers.c.access_token_encrypted,
            ).where(
                tables.whatsapp_numbers.c.tenant_id == uuid.UUID(tenant_id),
                tables.whatsapp_numbers.c.status == "connected",
            )
        )
    ).one_or_none()
    if number is None:
        logger.warning("Tenant sem número WhatsApp conectado | tenant=%s", tenant_id)
        return None

    credit_balance = (
        await session.execute(
            select(tables.tenants.c.credit_balance).where(
                tables.tenants.c.id == uuid.UUID(tenant_id)
            )
        )
    ).scalar_one()

    return InboundContext(
        conversation_state=conversation.state,
        contact_phone_number=conversation.contact_phone_number,
        message_content=content,
        phone_number_id=number.phone_number_id,
        access_token_encrypted=number.access_token_encrypted,
        credit_balance=credit_balance,
    )


async def _persist_agent_responses(
    session: AsyncSession,
    tenant_id: str,
    conversation_id: str,
    responses: list[str],
    tokens_used: int = 0,
    credits: int = 0,
    delivery_failures: set[int] | None = None,
) -> uuid.UUID | None:
    """Insere as respostas do agente e retorna o id da primeira.

    O consumo da execução inteira (tokens/créditos) fica registrado na
    primeira mensagem — é a ela que o lançamento do ledger se vincula.
    `delivery_failures` marca, por índice, quais respostas falharam ao
    entregar ao WhatsApp — a cobrança acontece independente disso, porque o
    custo do LLM já ocorreu.
    """
    delivery_failures = delivery_failures or set()
    now = datetime.now(UTC)
    first_message_id: uuid.UUID | None = None
    for i, response in enumerate(responses):
        values: dict = {
            "conversation_id": uuid.UUID(conversation_id),
            "tenant_id": uuid.UUID(tenant_id),
            "sender_type": "agent",
            "content": response,
            "delivery_status": "failed" if i in delivery_failures else "sent",
            "created_at": now,
        }
        if i == 0:
            values["tokens_used"] = tokens_used or None
            values["credits_consumed"] = credits or None
        result = await session.execute(
            insert(tables.messages).values(**values).returning(tables.messages.c.id)
        )
        if i == 0:
            first_message_id = result.scalar_one()
    if responses:
        await session.execute(
            update(tables.conversations)
            .where(tables.conversations.c.id == uuid.UUID(conversation_id))
            .values(last_message_at=now)
        )
    return first_message_id


async def _debitar_creditos(
    session: AsyncSession,
    tenant_id: str,
    message_id: uuid.UUID,
    tokens_used: int,
    credits: int,
) -> None:
    """Lança o consumo no ledger e atualiza o cache de saldo do tenant."""
    await session.execute(
        insert(tables.credit_transactions).values(
            tenant_id=uuid.UUID(tenant_id),
            type="consumption",
            amount_credits=-credits,
            related_message_id=message_id,
            description=f"Consumo do agente ({tokens_used} tokens)",
            created_at=datetime.now(UTC),
        )
    )
    await session.execute(
        update(tables.tenants)
        .where(tables.tenants.c.id == uuid.UUID(tenant_id))
        .values(credit_balance=tables.tenants.c.credit_balance - credits)
    )
