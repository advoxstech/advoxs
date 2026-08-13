"""Provisionamento de uma instância Z-API — compartilhado entre dois
chamadores: o self-service do próprio tenant (POST /whatsapp/connect-zapi,
`managed_by_advoxs=False`) e o fluxo manual do admin da plataforma (POST
/platform-admin/tenants/{id}/whatsapp/zapi, `managed_by_advoxs=True`).

O fluxo manual existe porque o Programa de Parceiro/Integrador da Z-API (que
automatizaria a criação de instâncias numa única conta) exige 10 instâncias
já contratadas ou um plano mínimo de R$899/mês — em vez disso, um funcionário
da Advoxs cria a instância manualmente no próprio painel da Z-API (preço
normal por instância) e atribui as credenciais aqui em nome do tenant; o
tenant nunca vê nem digita instance_id/token, só escaneia o QR code de
dentro do próprio painel (GET /whatsapp/zapi-qrcode, sem nenhuma mudança).
"""

import logging
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.zapi import (
    ZApiApiError,
    ZApiNetworkError,
    check_zapi_status,
    configure_zapi_webhook,
    fetch_zapi_connected_phone,
)
from app.core.config import settings
from app.core.crypto import encrypt_access_token
from app.models import WhatsAppNumber
from app.schemas.whatsapp_connection import WhatsAppConnectionOut

logger = logging.getLogger(__name__)


def mask_phone_number(value: str) -> str:
    """Mantém DDI (3 chars) e os 4 últimos dígitos visíveis; mascara o resto."""
    if len(value) <= 7:
        return value
    return f"{value[:3]} **** {value[-4:]}"


def to_connection_out(number: WhatsAppNumber) -> WhatsAppConnectionOut:
    return WhatsAppConnectionOut(
        provider=number.provider,
        display_phone_number=mask_phone_number(number.display_phone_number),
        status=number.status,
        connected_at=number.connected_at,
        managed_by_advoxs=number.zapi_managed_by_advoxs,
    )


async def provision_zapi_connection(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    instance_id: str,
    instance_token: str,
    client_token: str,
    *,
    managed_by_advoxs: bool,
) -> WhatsAppNumber:
    """Valida as credenciais na Z-API, configura o webhook e grava/atualiza a
    linha de `whatsapp_numbers` do tenant (adicionada à sessão, não
    commitada — cada chamador decide o próprio tratamento de IntegrityError
    e o status HTTP certo pro próprio contexto, tenant autenticado vs.
    admin).

    Propaga ZApiNetworkError/ZApiApiError sem tratar — mesma divisão de
    responsabilidade.
    """
    try:
        live_status = await check_zapi_status(instance_id, instance_token, client_token)
    except ZApiNetworkError as exc:
        logger.error("Falha de rede ao validar credenciais Z-API | erro=%s", exc)
        raise

    webhook_secret = secrets.token_urlsafe(32)
    base = settings.api_public_url.rstrip("/")
    webhook_url = f"{base}/api/v1/webhooks/zapi/{webhook_secret}"

    try:
        await configure_zapi_webhook(instance_id, instance_token, client_token, webhook_url)
    except ZApiNetworkError as exc:
        logger.error("Falha de rede ao configurar webhook Z-API | erro=%s", exc)
        raise

    # Cobre a instância já pareada fora do nosso fluxo (ex: testada direto no
    # painel da Z-API antes de ser atribuída aqui) — sem isso, o front recebe
    # status="disconnected" e tenta buscar um QR code que a Z-API se recusa a
    # gerar pra instância já conectada (ver fetch_zapi_qrcode).
    zapi_status = "disconnected"
    zapi_display_phone = "Aguardando pareamento"
    if live_status.get("connected"):
        zapi_status = "connected"
        try:
            phone = await fetch_zapi_connected_phone(instance_id, instance_token, client_token)
        except (ZApiNetworkError, ZApiApiError) as exc:
            logger.warning(
                "Falha ao buscar telefone de instância Z-API já conectada (best-effort) | erro=%s",
                exc,
            )
            phone = None
        if phone:
            zapi_display_phone = phone

    existing = await session.scalar(
        select(WhatsAppNumber).where(WhatsAppNumber.tenant_id == tenant_id)
    )
    encrypted_token = encrypt_access_token(instance_token)
    encrypted_client_token = encrypt_access_token(client_token)
    now = datetime.now(UTC)

    number = existing if existing is not None else WhatsAppNumber(tenant_id=tenant_id)

    number.provider = "zapi"
    number.zapi_instance_id = instance_id
    number.zapi_instance_token_encrypted = encrypted_token
    number.zapi_client_token_encrypted = encrypted_client_token
    number.zapi_webhook_secret = webhook_secret
    number.zapi_managed_by_advoxs = managed_by_advoxs
    # Simétrico ao branch Meta de connect() em app/api/v1/whatsapp.py — evita
    # uma linha inconsistente carregando credenciais Meta remanescentes.
    number.phone_number_id = None
    number.waba_id = None
    number.access_token_encrypted = None
    number.display_phone_number = zapi_display_phone
    number.status = zapi_status
    number.connected_at = now

    if existing is None:
        session.add(number)

    return number
