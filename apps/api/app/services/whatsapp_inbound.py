"""Processamento de mensagens entrantes do webhook do WhatsApp.

Fluxo: resolve o tenant pelo phone_number_id -> upsert da conversa ->
persiste a mensagem (dedup por wamid) -> enfileira o job no Arq. O worker
decide entre agente e humano (estado da conversa) e chama o agents service.
"""

import logging
from datetime import UTC, datetime

from arq.connections import ArqRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message, WhatsAppNumber
from app.schemas.whatsapp import InboundWhatsAppMessage, extract_inbound_messages

logger = logging.getLogger(__name__)


async def handle_meta_webhook(payload: dict, session: AsyncSession, arq: ArqRedis) -> dict:
    """Persiste as mensagens do payload e enfileira o processamento.

    Retorna um resumo ({"received": N}) — o corpo da resposta não importa
    para a Meta, só o status 200 rápido.
    """
    persisted: list[tuple[str, str, str]] = []  # (tenant_id, conversation_id, message_id)

    for inbound in extract_inbound_messages(payload):
        result = await _persist_inbound_message(inbound, session)
        if result is not None:
            persisted.append(result)

    await session.commit()

    # Enfileira só depois do commit — o worker não pode correr atrás de linha
    # ainda não visível.
    for tenant_id, conversation_id, message_id in persisted:
        await arq.enqueue_job(
            "process_inbound_message",
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )

    return {"received": len(persisted)}


async def _persist_inbound_message(
    inbound: InboundWhatsAppMessage, session: AsyncSession
) -> tuple[str, str, str] | None:
    number = await session.scalar(
        select(WhatsAppNumber).where(WhatsAppNumber.phone_number_id == inbound.phone_number_id)
    )
    if number is None:
        logger.warning("Webhook para phone_number_id desconhecido: %s", inbound.phone_number_id)
        return None

    # Dedup: a Meta reenvia webhooks não confirmados.
    duplicate = await session.scalar(
        select(Message.id).where(Message.wa_message_id == inbound.wa_message_id)
    )
    if duplicate is not None:
        logger.info("Webhook duplicado ignorado (wamid=%s)", inbound.wa_message_id)
        return None

    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.tenant_id == number.tenant_id,
            Conversation.contact_phone_number == inbound.contact_phone_number,
        )
    )
    if conversation is None:
        conversation = Conversation(
            tenant_id=number.tenant_id,
            contact_phone_number=inbound.contact_phone_number,
        )
        session.add(conversation)
        await session.flush()

    conversation.last_message_at = datetime.now(UTC)

    message = Message(
        conversation_id=conversation.id,
        tenant_id=number.tenant_id,
        sender_type="contact",
        content=inbound.content,
        media_url=inbound.media_id,  # ID de mídia da Meta; download fica para o worker
        media_type=inbound.media_type,
        wa_message_id=inbound.wa_message_id,
    )
    session.add(message)
    await session.flush()

    return (str(number.tenant_id), str(conversation.id), str(message.id))
