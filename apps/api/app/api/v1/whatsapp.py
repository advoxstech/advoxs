"""Conexão do número de WhatsApp Business do escritório (1:1 com tenant), por
um de dois provedores: **Meta** (Cloud API oficial) ou **Z-API** (não-oficial,
conexão por QR code).

Meta: o escritório faz o setup do lado da Meta (app, System User, token
permanente, verificação do número) e cola as credenciais aqui. Antes de
persistir, valida o token e registra o número na Cloud API — nada é salvo se
a Meta rejeitar.

Z-API: o escritório cola instance_id/token (gerados no painel da Z-API) e
escaneia um QR code — sem aprovação de negócio. Ver `connect_zapi`,
`get_zapi_qrcode` e `get_zapi_status` abaixo.
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
from app.clients.zapi import (
    ZApiApiError,
    ZApiNetworkError,
    check_zapi_status,
    disconnect_zapi_instance,
    fetch_zapi_connected_phone,
    fetch_zapi_qrcode,
)
from app.core.config import settings
from app.core.crypto import decrypt_access_token, encrypt_access_token
from app.models import WhatsAppNumber
from app.schemas.whatsapp_connection import (
    ConnectWhatsAppRequest,
    ConnectZApiRequest,
    WebhookConfigOut,
    WhatsAppConnectionOut,
    ZApiProvisioningRequestOut,
)
from app.services.zapi_connection import provision_zapi_connection, to_connection_out
from app.services.zapi_provisioning_requests import (
    create_or_get_pending_request,
    get_pending_request,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

_GRAPH_ERROR_DETAIL = "Falha ao comunicar com a Meta — tente novamente em instantes"
_ZAPI_ERROR_DETAIL = "Falha ao comunicar com a Z-API — tente novamente em instantes"


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
        existing.provider = "meta"
        existing.phone_number_id = body.phone_number_id
        existing.waba_id = body.waba_id
        existing.display_phone_number = display_phone_number
        existing.access_token_encrypted = encrypted
        # Limpa credenciais Z-API remanescentes — evita uma linha inconsistente
        # que ainda carrega instância Z-API depois do tenant migrar pra Meta.
        existing.zapi_instance_id = None
        existing.zapi_instance_token_encrypted = None
        existing.zapi_client_token_encrypted = None
        existing.zapi_webhook_secret = None
        existing.zapi_managed_by_advoxs = False
        existing.status = "connected"
        existing.connected_at = now
        number = existing
    else:
        number = WhatsAppNumber(
            tenant_id=ctx.tenant_id,
            provider="meta",
            phone_number_id=body.phone_number_id,
            waba_id=body.waba_id,
            display_phone_number=display_phone_number,
            access_token_encrypted=encrypted,
            status="connected",
            zapi_managed_by_advoxs=False,
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
    return to_connection_out(number)


@router.post("/connect-zapi")
async def connect_zapi(
    body: ConnectZApiRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> WhatsAppConnectionOut:
    try:
        number = await provision_zapi_connection(
            session,
            ctx.tenant_id,
            body.instance_id,
            body.instance_token,
            body.client_token,
            # O tenant está colando a própria conta Z-API — mesmo que esta
            # linha estivesse antes marcada como gerenciada pela Advoxs
            # (provisionada via admin), o tenant assumindo com credenciais
            # próprias sempre volta pro modelo self-service.
            managed_by_advoxs=False,
        )
    except ZApiNetworkError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_ZAPI_ERROR_DETAIL)
    except ZApiApiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

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


@router.get("/zapi-qrcode")
async def get_zapi_qrcode(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict:
    number = await session.scalar(
        select(WhatsAppNumber).where(
            WhatsAppNumber.tenant_id == ctx.tenant_id, WhatsAppNumber.provider == "zapi"
        )
    )
    if number is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Nenhuma instância Z-API conectada"
        )

    token = decrypt_access_token(number.zapi_instance_token_encrypted)
    client_token = (
        decrypt_access_token(number.zapi_client_token_encrypted)
        if number.zapi_client_token_encrypted
        else None
    )
    try:
        qrcode_base64 = await fetch_zapi_qrcode(number.zapi_instance_id, token, client_token)
    except ZApiNetworkError as exc:
        logger.error("Falha de rede ao buscar QR code | erro=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao comunicar com a Z-API — tente novamente em instantes",
        )
    except ZApiApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return {"qrcode_base64": qrcode_base64}


@router.post("/request-managed-zapi")
async def request_managed_zapi(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ZApiProvisioningRequestOut:
    """Pedido do tenant pra Advoxs configurar a conexão Z-API por ele — sem
    instance_id/token nenhum, só o pedido em si. Idempotente: repetir a
    chamada com um pedido já pendente devolve o mesmo, sem duplicar. Ver
    app/services/zapi_provisioning_requests.py."""
    request = await create_or_get_pending_request(session, ctx.tenant_id)
    return ZApiProvisioningRequestOut(status=request.status, requested_at=request.requested_at)


@router.get("/managed-zapi-request")
async def get_managed_zapi_request(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ZApiProvisioningRequestOut | None:
    request = await get_pending_request(session, ctx.tenant_id)
    if request is None:
        return None
    return ZApiProvisioningRequestOut(status=request.status, requested_at=request.requested_at)


async def _self_heal_zapi_status(number: WhatsAppNumber, session: AsyncSession) -> WhatsAppNumber:
    """Revalida o status Z-API ao vivo e promove `status`/`display_phone_number`
    no banco quando a Z-API já confirma o pareamento — best-effort (uma falha
    aqui nunca deve quebrar a tela, só devolve o último estado conhecido).

    Reaproveitada tanto pelo polling dedicado (`GET /zapi-status`, chamado
    a cada 3s enquanto a tela do QR code está aberta) quanto pelo carregamento
    normal da tela (`GET /connection`, chamado toda vez que a página abre) —
    sem essa segunda chamada, um tenant que fechasse a aba antes do polling
    detectar a conexão ficaria com `status="disconnected"` pra sempre, mesmo
    já pareado, e o worker descartaria toda mensagem recebida em silêncio
    (`_load_context` filtra `status == "connected"`).

    Também tenta de novo buscar o telefone quando `display_phone_number`
    ainda é o placeholder `"Aguardando pareamento"` mesmo com `status`
    já `"connected"` — cobre o caso em que a primeira promoção aconteceu
    num momento em que `fetch_zapi_connected_phone` não tinha o campo ainda
    disponível (pareamento incompleto)."""
    token = decrypt_access_token(number.zapi_instance_token_encrypted)
    client_token = (
        decrypt_access_token(number.zapi_client_token_encrypted)
        if number.zapi_client_token_encrypted
        else None
    )
    try:
        live_status = await check_zapi_status(number.zapi_instance_id, token, client_token)

        needs_phone_refresh = (
            number.status != "connected" or number.display_phone_number == "Aguardando pareamento"
        )
        if live_status.get("connected") and needs_phone_refresh:
            phone = await fetch_zapi_connected_phone(number.zapi_instance_id, token, client_token)
            if phone:
                number.display_phone_number = phone
            number.status = "connected"
            await session.commit()
            await session.refresh(number)
    except (ZApiNetworkError, ZApiApiError) as exc:
        logger.warning("Falha ao revalidar status Z-API (best-effort) | erro=%s", exc)

    return number


@router.get("/zapi-status")
async def get_zapi_status(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> WhatsAppConnectionOut | None:
    number = await session.scalar(
        select(WhatsAppNumber).where(
            WhatsAppNumber.tenant_id == ctx.tenant_id, WhatsAppNumber.provider == "zapi"
        )
    )
    if number is None:
        return None

    number = await _self_heal_zapi_status(number, session)
    return to_connection_out(number)


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
    if number.provider == "zapi":
        # Mesmo self-heal do polling dedicado (ver docstring) — sem isso, o
        # status só se corrige enquanto a tela do QR code está aberta.
        number = await _self_heal_zapi_status(number, session)
    return to_connection_out(number)


@router.get("/webhook-config")
async def get_webhook_config(
    ctx: TenantContext = Depends(get_current_tenant),
) -> WebhookConfigOut:
    """Valores que o escritório precisa colar no painel da Meta (passo manual do webhook).

    Só leitura de config — não toca em tabela nenhuma, então não usa
    get_tenant_session; a autenticação de tenant continua obrigatória.
    """
    base = settings.api_public_url.rstrip("/")
    return WebhookConfigOut(
        callback_url=f"{base}/api/v1/webhooks/whatsapp",
        verify_token=settings.meta_verify_token,
    )


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

    if number.provider == "zapi":
        # Falha ao avisar a Z-API não deve travar o tenant desconectando
        # localmente — best-effort, mesmo espírito de outras integrações
        # externas neste código-base (ex: falha ao mandar confirmação de
        # pagamento não desfaz o crédito já commitado).
        token = decrypt_access_token(number.zapi_instance_token_encrypted)
        client_token = (
            decrypt_access_token(number.zapi_client_token_encrypted)
            if number.zapi_client_token_encrypted
            else None
        )
        try:
            await disconnect_zapi_instance(number.zapi_instance_id, token, client_token)
        except (ZApiNetworkError, ZApiApiError) as exc:
            logger.warning("Falha ao desconectar na Z-API (best-effort) | erro=%s", exc)

    number.status = "disconnected"
    await session.commit()
    await session.refresh(number)
    return to_connection_out(number)
