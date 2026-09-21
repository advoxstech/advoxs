import hashlib
import hmac
import json

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_whatsapp_secret
from app.core.db import get_system_session
from app.core.queue import get_arq_pool
from app.models import WhatsAppNumber
from app.services.whatsapp_inbound import handle_meta_webhook

router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])


@router.get("")
async def verify_webhook(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
) -> PlainTextResponse:
    """Verificação de assinatura do webhook exigida pela Meta ao configurar a URL."""
    if hub_mode == "subscribe" and hmac.compare_digest(
        hub_verify_token, settings.meta_verify_token
    ):
        return PlainTextResponse(hub_challenge)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Token de verificação inválido"
    )


@router.get("/{webhook_secret}")
async def verify_tenant_webhook(
    webhook_secret: str,
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
    session: AsyncSession = Depends(get_system_session),
) -> PlainTextResponse:
    number = await session.scalar(
        select(WhatsAppNumber).where(
            WhatsAppNumber.provider == "meta",
            WhatsAppNumber.meta_webhook_secret == webhook_secret,
        )
    )
    if (
        number is not None
        and hub_mode == "subscribe"
        and hmac.compare_digest(hub_verify_token, webhook_secret)
    ):
        return PlainTextResponse(hub_challenge)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Token de verificação inválido"
    )


@router.post("")
@router.post("/{webhook_secret}")
async def receive_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    session: AsyncSession = Depends(get_system_session),
    arq: ArqRedis = Depends(get_arq_pool),
    webhook_secret: str | None = None,
) -> dict:
    raw_body = await request.body()

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload inválido")

    if webhook_secret is None:
        _verify_signature(raw_body, x_hub_signature_256)
    else:
        await _verify_tenant_signature(
            raw_body, payload, x_hub_signature_256, webhook_secret, session
        )

    return await handle_meta_webhook(payload, session, arq)


async def _verify_tenant_signature(
    raw_body: bytes,
    payload: dict,
    signature_header: str | None,
    webhook_secret: str,
    session: AsyncSession,
) -> None:
    """Valida o webhook com o segredo do app Meta do próprio tenant."""
    if settings.app_env != "production" and signature_header is None:
        return
    phone_number_id = _phone_number_id(payload)
    if phone_number_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Número Meta ausente")
    number = await session.scalar(
        select(WhatsAppNumber).where(
            WhatsAppNumber.provider == "meta",
            WhatsAppNumber.phone_number_id == phone_number_id,
        )
    )
    if (
        number is None
        or number.meta_app_secret_encrypted is None
        or not hmac.compare_digest(number.meta_webhook_secret or "", webhook_secret)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Webhook Meta desconhecido"
        )
    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Assinatura ausente")
    expected = hmac.new(
        decrypt_whatsapp_secret(number.meta_app_secret_encrypted).encode(), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature_header.removeprefix("sha256="), expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Assinatura inválida")


def _phone_number_id(payload: dict) -> str | None:
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            phone_number_id = change.get("value", {}).get("metadata", {}).get("phone_number_id")
            if isinstance(phone_number_id, str) and phone_number_id:
                return phone_number_id
    return None


def _verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    """Valida o X-Hub-Signature-256 (HMAC-SHA256 do corpo com o app secret).

    Se META_APP_SECRET não estiver setado (dev local), a validação é ignorada.
    """
    if not settings.meta_app_secret:
        if settings.app_env == "production":
            raise HTTPException(status_code=503, detail="Validação de assinatura indisponível")
        return
    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Assinatura ausente")

    expected = hmac.new(settings.meta_app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    if not hmac.compare_digest(received, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Assinatura inválida")
