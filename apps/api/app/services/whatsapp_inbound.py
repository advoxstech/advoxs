"""Processamento de mensagens entrantes dos webhooks de WhatsApp (Meta e
Z-API). Fluxo: resolve o tenant (por phone_number_id ou zapi_instance_id,
conforme o provedor) -> upsert da conversa -> persiste a mensagem (dedup
por wa_message_id) -> enfileira o job no Arq. O worker decide entre agente
e humano (estado da conversa) e chama o agents service.
"""

import gzip
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime

from arq.connections import ArqRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.omni import valid_webhook_signature
from app.core.crypto import decrypt_access_token
from app.core.safe_logging import safe_error, safe_identifier
from app.models import Conversation, InboundMessageJob, Message, WhatsAppNumber
from app.schemas.whatsapp import extract_inbound_messages, extract_inbound_zapi_message
from app.services.urgency import flag_contact_message

logger = logging.getLogger(__name__)


async def handle_meta_webhook(payload: dict, session: AsyncSession, arq: ArqRedis) -> dict:
    """Persiste as mensagens do payload e enfileira o processamento.

    Retorna um resumo ({"received": N}) — o corpo da resposta não importa
    para a Meta, só o status 200 rápido.
    """
    persisted: list[tuple[str, str, str, str]] = []

    for inbound in extract_inbound_messages(payload):
        number = await session.scalar(
            select(WhatsAppNumber).where(WhatsAppNumber.phone_number_id == inbound.phone_number_id)
        )
        if number is None:
            logger.warning(
                "Webhook Meta para número desconhecido | provider_ref=%s",
                safe_identifier(inbound.phone_number_id),
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
    for tenant_id, conversation_id, message_id, job_id in persisted:
        try:
            await arq.enqueue_job(
                "process_inbound_message",
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                message_id=message_id,
                job_id=job_id,
            )
        except Exception as exc:
            # A mensagem e a pendência já foram gravadas. Responder 200 evita
            # depender de uma nova entrega do provedor; o worker reenfileira.
            logger.error(
                "Fila indisponível; pendência será recuperada | error_type=%s",
                safe_error(exc),
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
            "Webhook Z-API com segredo ou instância inválidos | provider_ref=%s",
            safe_identifier(inbound.zapi_instance_id),
        )
        return {"received": 0}

    return await _store_zapi_format_message(number, inbound, session, arq)


async def handle_omni_webhook(
    raw_body: bytes,
    path_secret: str,
    signature: str | None,
    idempotency_key: str | None,
    content_encoding: str | None,
    session: AsyncSession,
    arq: ArqRedis,
) -> dict:
    """Webhook do Z-API Omni (WhatsApp oficial), no formato Z-API.

    O número é resolvido pelo segredo da URL e a requisição só é aceita com a
    assinatura HMAC válida. Mensagens seguem o mesmo caminho da Z-API; eventos
    de conexão atualizam o status do número.
    """
    number = await session.scalar(
        select(WhatsAppNumber).where(
            WhatsAppNumber.provider == "omni",
            WhatsAppNumber.omni_webhook_path_secret == path_secret,
        )
    )
    if number is None or not number.omni_webhook_signing_secret_encrypted:
        logger.warning("Webhook Omni com segredo de URL desconhecido")
        raise WebhookRejected()
    signing_secret = decrypt_access_token(number.omni_webhook_signing_secret_encrypted)
    if not valid_webhook_signature(
        signing_secret, raw_body, signature, idempotency_key, content_encoding
    ):
        logger.warning(
            "Webhook Omni com assinatura inválida | provider_ref=%s",
            safe_identifier(number.omni_channel_id or ""),
        )
        raise WebhookRejected()

    body = gzip.decompress(raw_body) if content_encoding == "gzip" else raw_body
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"received": 0}
    if not isinstance(payload, dict):
        return {"received": 0}

    event = payload.get("type")
    if event in ("ConnectedCallback", "DisconnectedCallback"):
        number.status = "connected" if event == "ConnectedCallback" else "disconnected"
        await session.commit()
        logger.info(
            "Conexão do canal Omni atualizada | provider_ref=%s status=%s",
            safe_identifier(number.omni_channel_id or ""),
            number.status,
        )
        return {"received": 0}

    # O número já foi resolvido pelo segredo da URL; o canal entra como
    # instância caso o corpo não traga o campo.
    payload.setdefault("instanceId", number.omni_channel_id)
    inbound = extract_inbound_zapi_message(payload)
    if inbound is None:
        return {"received": 0}
    return await _store_zapi_format_message(number, inbound, session, arq)


class WebhookRejected(Exception):
    """Webhook sem autenticação válida (segredo da URL ou assinatura)."""


async def _store_zapi_format_message(
    number: WhatsAppNumber, inbound, session: AsyncSession, arq: ArqRedis
) -> dict:
    """Grava a mensagem (formato Z-API) e enfileira o turno — Z-API e Omni."""
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
    tenant_id, conversation_id, message_id, job_id = result
    try:
        await arq.enqueue_job(
            "process_inbound_message",
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message_id=message_id,
            job_id=job_id,
        )
    except Exception as exc:
        logger.error(
            "Fila indisponível; pendência será recuperada | error_type=%s", safe_error(exc)
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
) -> tuple[str, str, str, str] | None:
    # Dedup: ambos os provedores podem reentregar webhook não confirmado.
    duplicate_message_id = await session.scalar(
        select(Message.id).where(Message.wa_message_id == wa_message_id)
    )
    if duplicate_message_id is not None:
        logger.info(
            "Webhook duplicado ignorado | provider_message_ref=%s",
            safe_identifier(wa_message_id),
        )
        pending_job = await session.scalar(
            select(InboundMessageJob).where(
                InboundMessageJob.message_id == duplicate_message_id,
                InboundMessageJob.status == "pending",
            )
        )
        if pending_job is not None:
            return (
                str(pending_job.tenant_id),
                str(pending_job.conversation_id),
                str(pending_job.message_id),
                str(pending_job.id),
            )
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

    # Triggers resolve registered contacts atomically with conversation creation.
    # Refresh defaults modified by PostgreSQL before deciding whether to process AI.
    await session.refresh(conversation)

    conversation.last_message_at = datetime.now(UTC)
    if conversation.state == "agent":
        conversation.automation_status = "processing"

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
    await flag_contact_message(session, conversation, content)

    job = InboundMessageJob(
        id=uuid.uuid4(),
        tenant_id=number.tenant_id,
        conversation_id=conversation.id,
        message_id=message.id,
    )
    session.add(job)
    await session.flush()

    return (str(number.tenant_id), str(conversation.id), str(message.id), str(job.id))
