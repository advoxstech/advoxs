import json

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_system_session
from app.core.queue import get_arq_pool
from app.services.whatsapp_inbound import handle_zapi_webhook

router = APIRouter(prefix="/webhooks/zapi", tags=["webhooks"])


@router.post("/{webhook_secret}")
async def receive_webhook(
    webhook_secret: str,
    request: Request,
    session: AsyncSession = Depends(get_system_session),
    arq: ArqRedis = Depends(get_arq_pool),
) -> dict:
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload inválido")

    return await handle_zapi_webhook(payload, webhook_secret, session, arq)
