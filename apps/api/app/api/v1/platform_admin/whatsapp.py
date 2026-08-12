"""Fluxo manual de conexão Z-API por tenant, feito pelo admin da plataforma —
ver docstring de app/services/zapi_connection.py pro porquê de existir (o
Programa de Parceiro/Integrador da Z-API, que automatizaria a criação de
instâncias, exige 10 instâncias contratadas ou R$899/mês).

Um funcionário da Advoxs cria a instância manualmente no painel da própria
Z-API e atribui as credenciais aqui em nome de um tenant específico — o
tenant nunca vê nem digita instance_id/token, só escaneia o QR code de
dentro do próprio painel (GET /whatsapp/zapi-qrcode, endpoint já existente,
sem nenhuma mudança).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PlatformAdminContext, get_current_platform_admin
from app.clients.zapi import ZApiApiError, ZApiNetworkError
from app.core.db import get_system_session
from app.models import AdminAuditLog, Tenant, WhatsAppNumber
from app.schemas.whatsapp_connection import ConnectZApiRequest, WhatsAppConnectionOut
from app.services.zapi_connection import provision_zapi_connection, to_connection_out

router = APIRouter(prefix="/platform-admin/tenants", tags=["platform-admin"])

_ZAPI_ERROR_DETAIL = "Falha ao comunicar com a Z-API — tente novamente em instantes"


@router.get("/{tenant_id}/whatsapp")
async def get_tenant_whatsapp_route(
    tenant_id: uuid.UUID,
    admin: PlatformAdminContext = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_system_session),
) -> WhatsAppConnectionOut | None:
    number = await session.scalar(
        select(WhatsAppNumber).where(WhatsAppNumber.tenant_id == tenant_id)
    )
    if number is None:
        return None
    return to_connection_out(number)


@router.post("/{tenant_id}/whatsapp/zapi")
async def provision_tenant_zapi_route(
    tenant_id: uuid.UUID,
    body: ConnectZApiRequest,
    admin: PlatformAdminContext = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_system_session),
) -> WhatsAppConnectionOut:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant não encontrado")

    try:
        number = await provision_zapi_connection(
            session,
            tenant_id,
            body.instance_id,
            body.instance_token,
            body.client_token,
            managed_by_advoxs=True,
        )
    except ZApiNetworkError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_ZAPI_ERROR_DETAIL)
    except ZApiApiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Mesma auditoria de get_tenant_detail (app/services/admin_tenants.py) —
    # atribuir credenciais em nome de um tenant também atravessa o
    # isolamento normal por tenant_id.
    session.add(AdminAuditLog(platform_admin_id=admin.admin_id, tenant_id=tenant_id))

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta instância já está conectada a outro escritório",
        )
    await session.refresh(number)
    return to_connection_out(number)
