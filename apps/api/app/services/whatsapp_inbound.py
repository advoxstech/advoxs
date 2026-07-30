"""Processamento de mensagens entrantes dos webhooks de WhatsApp (Meta e
Z-API). Fluxo: resolve o tenant (por phone_number_id ou zapi_instance_id,
conforme o provedor) -> upsert da conversa -> persiste a mensagem (dedup
por wa_message_id) -> enfileira o job no Arq. O worker decide entre agente
e humano (estado da conversa) e chama o agents service.
"""

import hmac
import logging
from datetime import UTC, datetime

from arq.connections import ArqRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message, WhatsAppNumber
from app.schemas.whatsapp import extract_inbound_messages, extract_inbound_zapi_message

logger = logging.getLogger(__name__)


async def handle_meta_webhook(payload: dict, session: AsyncSession, arq: ArqRedis) -> dict:
    """Persiste as mensagens do payload e enfileira o processamento.

    Retorna um resumo ({"received": N}) — o corpo da resposta não importa
    para a Meta, só o status 200 rápido.
    """
    persisted: list[tuple[str, str, str]] = []  # (tenant_id, conversation_id, message_id)

    for inbound in extract_inbound_messages(payload):
        number = await session.scalar(
            select(WhatsAppNumber).where(WhatsAppNumber.phone_number_id == inbound.phone_number_id)
        )
        if number is None:
            logger.warning(
                "Webhook Meta para phone_number_id desconhecido: %s", inbound.phone_number_id
            )
            continue
        result = await _persist_inbound_message(
            number,
            contact_phone_number=inbound.contact_phone_number,
            wa_message_id=inbound.wa_message_id,
            content=inbound.content,
            media_id=inbound.media_id,
            media_type=inbound.media_type,
            session=session,
        )
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


async def handle_zapi_webhook(
    payload: dict, webhook_secret: str, session: AsyncSession, arq: ArqRedis
) -> dict:
    """Mesma forma de `handle_meta_webhook`, mas resolve o tenant por
    zapi_instance_id + confere o segredo do path (única autenticação do
    endpoint, já que a Z-API não assina o payload)."""
    inbound = extract_inbound_zapi_message(payload)
    if inbound is None:
        return {"received": 0}

    number = await session.scalar(
        select(WhatsAppNumber).where(
            WhatsAppNumber.provider == "zapi",
            WhatsAppNumber.zapi_instance_id == inbound.zapi_instance_id,
        )
    )
    if number is None or not hmac.compare_digest(number.zapi_webhook_secret or "", webhook_secret):
        logger.warning(
            "Webhook Z-API com segredo ou instância inválidos | instance=%s",
            inbound.zapi_instance_id,
        )
        return {"received": 0}

    result = await _persist_inbound_message(
        number,
        contact_phone_number=inbound.contact_phone_number,
        wa_message_id=inbound.wa_message_id,
        content=inbound.content,
        # Diferente da Meta, a Z-API já entrega a URL de mídia pronta no
        # próprio payload — não é um ID opaco, então media_id aqui já é a
        # URL final (o parâmetro é forwardado direto pra Message.media_url).
        media_id=inbound.media_url,
        media_type=inbound.media_type,
        session=session,
    )
    if result is None:
        return {"received": 0}

    await session.commit()
    tenant_id, conversation_id, message_id = result
    await arq.enqueue_job(
        "process_inbound_message",
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    return {"received": 1}


async def _persist_inbound_message(
    number: WhatsAppNumber,
    *,
    contact_phone_number: str,
    wa_message_id: str,
    content: str,
    media_id: str | None,
    media_type: str | None,
    session: AsyncSession,
) -> tuple[str, str, str] | None:
    # Dedup: ambos os provedores podem reentregar webhook não confirmado.
    duplicate = await session.scalar(
        select(Message.id).where(Message.wa_message_id == wa_message_id)
    )
    if duplicate is not None:
        logger.info("Webhook duplicado ignorado (wamid=%s)", wa_message_id)
        return None

    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.tenant_id == number.tenant_id,
            Conversation.contact_phone_number == contact_phone_number,
        )
    )
    if conversation is None:
        conversation = Conversation(
            tenant_id=number.tenant_id,
            contact_phone_number=contact_phone_number,
        )
        session.add(conversation)
        await session.flush()

    conversation.last_message_at = datetime.now(UTC)

    message = Message(
        conversation_id=conversation.id,
        tenant_id=number.tenant_id,
        sender_type="contact",
        content=content,
        media_url=media_id,  # ID de mídia da Meta; download fica para o worker
        media_type=media_type,
        wa_message_id=wa_message_id,
    )
    session.add(message)
    await session.flush()

    return (str(number.tenant_id), str(conversation.id), str(message.id))
