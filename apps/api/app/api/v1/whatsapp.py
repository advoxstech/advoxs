"""Conexão manual do número de WhatsApp Business do escritório (1:1 com tenant).

O escritório faz o setup do lado da Meta (app, System User, token permanente,
verificação do número) e cola as credenciais aqui. Antes de persistir, valida
o token e registra o número na Cloud API — nada é salvo se a Meta rejeitar.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.whatsapp import (
    WhatsAppApiError,
    WhatsAppNetworkError,
    fetch_display_phone_number,
    register_number,
    subscribe_app_to_waba,
)
from app.core.crypto import encrypt_access_token
from app.models import WhatsAppNumber
from app.schemas.whatsapp_connection import ConnectWhatsAppRequest, WhatsAppConnectionOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

_GRAPH_ERROR_DETAIL = "Falha ao comunicar com a Meta — tente novamente em instantes"


def _mask_phone_number(value: str) -> str:
    """Mantém DDI (3 chars) e os 4 últimos dígitos visíveis; mascara o resto."""
    if len(value) <= 7:
        return value
    return f"{value[:3]} **** {value[-4:]}"


def _to_out(number: WhatsAppNumber) -> WhatsAppConnectionOut:
    return WhatsAppConnectionOut(
        display_phone_number=_mask_phone_number(number.display_phone_number),
        status=number.status,
        connected_at=number.connected_at,
    )


@router.post("/connect")
async def connect(
    body: ConnectWhatsAppRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> WhatsAppConnectionOut:
    try:
        display_phone_number = await fetch_display_phone_number(
            body.phone_number_id, body.access_token
        )
    except WhatsAppNetworkError as exc:
        logger.error("Falha de rede ao validar número | erro=%s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_GRAPH_ERROR_DETAIL)
    except WhatsAppApiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    try:
        await register_number(body.phone_number_id, body.access_token, body.pin)
    except WhatsAppNetworkError as exc:
        logger.error("Falha de rede ao registrar número | erro=%s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_GRAPH_ERROR_DETAIL)
    except WhatsAppApiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    try:
        await subscribe_app_to_waba(body.waba_id, body.access_token)
    except WhatsAppNetworkError as exc:
        logger.error("Falha de rede ao inscrever app na WABA | erro=%s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_GRAPH_ERROR_DETAIL)
    except WhatsAppApiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    existing = await session.scalar(
        select(WhatsAppNumber).where(WhatsAppNumber.tenant_id == ctx.tenant_id)
    )
    encrypted = encrypt_access_token(body.access_token)
    now = datetime.now(UTC)

    if existing is not None:
        existing.phone_number_id = body.phone_number_id
        existing.waba_id = body.waba_id
        existing.display_phone_number = display_phone_number
        existing.access_token_encrypted = encrypted
        existing.status = "connected"
        existing.connected_at = now
        number = existing
    else:
        number = WhatsAppNumber(
            tenant_id=ctx.tenant_id,
            phone_number_id=body.phone_number_id,
            waba_id=body.waba_id,
            display_phone_number=display_phone_number,
            access_token_encrypted=encrypted,
            status="connected",
        )
        session.add(number)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este número já está conectado a outro escritório",
        )
    await session.refresh(number)
    return _to_out(number)


@router.get("/connection")
async def get_connection(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> WhatsAppConnectionOut | None:
    number = await session.scalar(
        select(WhatsAppNumber).where(WhatsAppNumber.tenant_id == ctx.tenant_id)
    )
    if number is None:
        return None
    return _to_out(number)


@router.post("/disconnect")
async def disconnect(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> WhatsAppConnectionOut:
    number = await session.scalar(
        select(WhatsAppNumber).where(WhatsAppNumber.tenant_id == ctx.tenant_id)
    )
    if number is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nenhum número conectado")

    number.status = "disconnected"
    await session.commit()
    await session.refresh(number)
    return _to_out(number)
