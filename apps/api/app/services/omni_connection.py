"""Cadastro de um número de WhatsApp oficial via Z-API Omni.

O workspace do Omni é da Advoxs e o número fica no portfólio da Advoxs na
Meta. Um funcionário conecta o número pelo painel do Omni e cadastra aqui o
canal (`channel_id` + token) em nome do escritório (painel de admin). O
escritório nunca vê o Omni nem as chaves.

Ao cadastrar, criamos no canal um webhook no formato Z-API, assinado (HMAC),
apontando para /api/v1/webhooks/omni/{segredo}.
"""

import logging
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.omni import (
    OmniApiError,
    OmniNetworkError,
    create_channel_webhook,
    delete_channel_webhook,
    get_channel,
)
from app.core.config import settings
from app.core.crypto import encrypt_access_token
from app.core.safe_logging import safe_error
from app.models import WhatsAppNumber

logger = logging.getLogger(__name__)

DEFAULT_DISPLAY = "WhatsApp oficial"


def clear_omni_fields(number: WhatsAppNumber) -> None:
    """Zera o bloco Omni ao trocar o número de provedor."""
    number.omni_channel_id = None
    number.omni_channel_token_encrypted = None
    number.omni_webhook_id = None
    number.omni_webhook_signing_secret_encrypted = None
    number.omni_webhook_path_secret = None


async def remove_omni_webhook(number: WhatsAppNumber) -> None:
    """Remove no Omni o webhook que criamos (melhor esforço)."""
    if not (number.omni_channel_id and number.omni_webhook_id):
        return
    try:
        await delete_channel_webhook(number.omni_channel_id, number.omni_webhook_id)
    except (OmniNetworkError, OmniApiError) as exc:
        logger.warning(
            "Falha ao remover webhook no Omni (best-effort) | error_type=%s", safe_error(exc)
        )


async def provision_omni_connection(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    channel_id: str,
    channel_token: str,
    *,
    display_phone_number: str | None = None,
    waba_id: str | None = None,
) -> WhatsAppNumber:
    """Valida o canal, cria o webhook e grava/atualiza o número do escritório.

    Adiciona à sessão sem commitar (o chamador trata IntegrityError). Propaga
    OmniNetworkError/OmniApiError.
    """
    # Consulta o canal para conferir que ele existe e pegar número/WABA.
    # Sem a função ENTERPRISE a rota não existe (None) e usamos o que o admin
    # informou; um canal inexistente responde 404 e interrompe o cadastro.
    channel = await get_channel(channel_id)

    existing = await session.scalar(
        select(WhatsAppNumber).where(WhatsAppNumber.tenant_id == tenant_id)
    )
    if existing is not None and existing.provider == "omni":
        # Reconexão: o webhook antigo deixa de ser usado.
        await remove_omni_webhook(existing)

    path_secret = secrets.token_urlsafe(32)
    base = settings.api_public_url.rstrip("/")
    webhook_id, signing_secret = await create_channel_webhook(
        channel_id, f"{base}/api/v1/webhooks/omni/{path_secret}"
    )

    display = display_phone_number
    status = "connected"
    if channel is not None:
        display = channel.get("phoneNumberConnected") or display
        waba_id = channel.get("businessId") or waba_id
        if channel.get("whatsappConnected") is False:
            status = "disconnected"

    number = existing if existing is not None else WhatsAppNumber(tenant_id=tenant_id)
    number.provider = "omni"
    number.omni_channel_id = channel_id
    number.omni_channel_token_encrypted = encrypt_access_token(channel_token)
    number.omni_webhook_id = webhook_id
    number.omni_webhook_signing_secret_encrypted = encrypt_access_token(signing_secret)
    number.omni_webhook_path_secret = path_secret
    # waba_id serve aos modelos de mensagem (follow-up), como na Meta direta.
    number.waba_id = waba_id
    # Simétrico aos outros provedores: nenhuma credencial de outro bloco fica para trás.
    number.phone_number_id = None
    number.access_token_encrypted = None
    number.meta_app_secret_encrypted = None
    number.meta_webhook_secret = None
    number.zapi_instance_id = None
    number.zapi_instance_token_encrypted = None
    number.zapi_client_token_encrypted = None
    number.zapi_webhook_secret = None
    number.zapi_managed_by_advoxs = False
    number.display_phone_number = display or DEFAULT_DISPLAY
    number.status = status
    number.connected_at = datetime.now(UTC)
    if existing is None:
        session.add(number)
    return number
