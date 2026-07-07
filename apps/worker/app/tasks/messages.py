import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from arq.worker import Retry
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import tables
from app.clients.agents import send_message_to_agents
from app.crypto import decrypt_access_token

logger = logging.getLogger(__name__)


@dataclass
class InboundContext:
    conversation_state: str
    contact_phone_number: str
    message_content: str
    phone_number_id: str
    access_token_encrypted: str


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

    access_token = decrypt_access_token(inbound.access_token_encrypted)

    try:
        responses = await send_message_to_agents(
            http,
            tenant_id=tenant_id,
            contact_phone_number=inbound.contact_phone_number,
            message=inbound.message_content,
            phone_number_id=inbound.phone_number_id,
            access_token=access_token,
        )
    except httpx.HTTPError as exc:
        # Erro transiente (rede, 5xx): reagenda com backoff crescente.
        logger.warning(
            "Falha ao chamar agents, reagendando | tenant=%s conversation=%s erro=%s",
            tenant_id,
            conversation_id,
            exc,
        )
        raise Retry(defer=ctx.get("job_try", 1) * 10)

    if responses is None:
        # 202: debounce agrupou em execução já em andamento.
        logger.info(
            "Mensagem agrupada pelo debounce do agents | tenant=%s conversation=%s",
            tenant_id,
            conversation_id,
        )
        return

    async with session_factory() as session:
        await _persist_agent_responses(session, tenant_id, conversation_id, responses)
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

    return InboundContext(
        conversation_state=conversation.state,
        contact_phone_number=conversation.contact_phone_number,
        message_content=content,
        phone_number_id=number.phone_number_id,
        access_token_encrypted=number.access_token_encrypted,
    )


async def _persist_agent_responses(
    session: AsyncSession, tenant_id: str, conversation_id: str, responses: list[str]
) -> None:
    now = datetime.now(UTC)
    for response in responses:
        await session.execute(
            insert(tables.messages).values(
                conversation_id=uuid.UUID(conversation_id),
                tenant_id=uuid.UUID(tenant_id),
                sender_type="agent",
                content=response,
                created_at=now,
            )
        )
    if responses:
        await session.execute(
            update(tables.conversations)
            .where(tables.conversations.c.id == uuid.UUID(conversation_id))
            .values(last_message_at=now)
        )
