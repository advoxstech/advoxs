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
import secrets
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
    configure_zapi_webhook,
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
)

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
        provider=number.provider,
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


@router.post("/connect-zapi")
async def connect_zapi(
    body: ConnectZApiRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> WhatsAppConnectionOut:
    try:
        live_status = await check_zapi_status(
            body.instance_id, body.instance_token, body.client_token
        )
    except ZApiNetworkError as exc:
        logger.error("Falha de rede ao validar credenciais Z-API | erro=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao comunicar com a Z-API — tente novamente em instantes",
        )
    except ZApiApiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    webhook_secret = secrets.token_urlsafe(32)
    base = settings.api_public_url.rstrip("/")
    webhook_url = f"{base}/api/v1/webhooks/zapi/{webhook_secret}"

    try:
        await configure_zapi_webhook(
            body.instance_id, body.instance_token, body.client_token, webhook_url
        )
    except ZApiNetworkError as exc:
        logger.error("Falha de rede ao configurar webhook Z-API | erro=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao comunicar com a Z-API — tente novamente em instantes",
        )
    except ZApiApiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Cobre o tenant que cola credenciais de uma instância já pareada fora do
    # nosso fluxo (ex: testada direto no painel da Z-API antes de conectar
    # aqui) — sem isso, o front recebe status="disconnected" e tenta buscar
    # um QR code que a Z-API se recusa a gerar pra instância já conectada
    # (ver fetch_zapi_qrcode em app/clients/zapi.py).
    zapi_status = "disconnected"
    zapi_display_phone = "Aguardando pareamento"
    if live_status.get("connected"):
        zapi_status = "connected"
        try:
            phone = await fetch_zapi_connected_phone(
                body.instance_id, body.instance_token, body.client_token
            )
        except (ZApiNetworkError, ZApiApiError) as exc:
            logger.warning(
                "Falha ao buscar telefone de instância Z-API já conectada (best-effort) | erro=%s",
                exc,
            )
            phone = None
        if phone:
            zapi_display_phone = phone

    existing = await session.scalar(
        select(WhatsAppNumber).where(WhatsAppNumber.tenant_id == ctx.tenant_id)
    )
    encrypted_token = encrypt_access_token(body.instance_token)
    encrypted_client_token = encrypt_access_token(body.client_token) if body.client_token else None
    now = datetime.now(UTC)

    if existing is not None:
        existing.provider = "zapi"
        existing.zapi_instance_id = body.instance_id
        existing.zapi_instance_token_encrypted = encrypted_token
        existing.zapi_client_token_encrypted = encrypted_client_token
        existing.zapi_webhook_secret = webhook_secret
        existing.phone_number_id = None
        existing.waba_id = None
        existing.access_token_encrypted = None
        existing.display_phone_number = zapi_display_phone
        existing.status = zapi_status
        # Simétrico ao branch Meta de connect() acima — reconectar (mesmo
        # trocando de provedor) sempre reseta connected_at pro instante atual.
        existing.connected_at = now
        number = existing
    else:
        number = WhatsAppNumber(
            tenant_id=ctx.tenant_id,
            provider="zapi",
            zapi_instance_id=body.instance_id,
            zapi_instance_token_encrypted=encrypted_token,
            zapi_client_token_encrypted=encrypted_client_token,
            zapi_webhook_secret=webhook_secret,
            display_phone_number=zapi_display_phone,
            status=zapi_status,
            connected_at=now,
        )
        session.add(number)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta instância já está conectada a outro escritório",
        )
    await session.refresh(number)
    return _to_out(number)


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
    if number.provider == "zapi":
        # Mesmo self-heal do polling dedicado (ver docstring) — sem isso, o
        # status só se corrige enquanto a tela do QR code está aberta.
        number = await _self_heal_zapi_status(number, session)
    return _to_out(number)


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
    return _to_out(number)
