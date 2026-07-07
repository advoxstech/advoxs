import hashlib
import hmac
import json

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.queue import get_arq_pool
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


@router.post("")
async def receive_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
    arq: ArqRedis = Depends(get_arq_pool),
) -> dict:
    raw_body = await request.body()
    _verify_signature(raw_body, x_hub_signature_256)

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload inválido")

    return await handle_meta_webhook(payload, session, arq)


def _verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    """Valida o X-Hub-Signature-256 (HMAC-SHA256 do corpo com o app secret).

    Se META_APP_SECRET não estiver setado (dev local), a validação é ignorada.
    """
    if not settings.meta_app_secret:
        return
    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Assinatura ausente")

    expected = hmac.new(settings.meta_app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    if not hmac.compare_digest(received, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Assinatura inválida")
