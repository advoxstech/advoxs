"""Webhook do Z-API Omni (WhatsApp oficial).

O Omni exige resposta 2xx em até 5 s e reenvia em caso de 408/429/5xx; um
4xx recusa o evento sem novas tentativas. A mensagem é gravada e enfileirada;
o processamento pesado fica no worker.
"""

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_system_session
from app.core.queue import get_arq_pool
from app.services.whatsapp_inbound import WebhookRejected, handle_omni_webhook

router = APIRouter(prefix="/webhooks/omni", tags=["webhooks"])


@router.post("/{path_secret}")
async def receive_webhook(
    path_secret: str,
    request: Request,
    session: AsyncSession = Depends(get_system_session),
    arq: ArqRedis = Depends(get_arq_pool),
) -> dict:
    try:
        return await handle_omni_webhook(
            await request.body(),
            path_secret,
            request.headers.get("x-webhook-signature"),
            request.headers.get("x-idempotency-key"),
            request.headers.get("content-encoding"),
            session,
            arq,
        )
    except WebhookRejected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Não autorizado")
