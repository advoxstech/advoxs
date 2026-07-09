import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PlatformAdminContext, get_current_platform_admin
from app.clients.agents import AgentsApiError, AgentsNetworkError
from app.core.db import get_session
from app.schemas.playground import PlaygroundMessageOut, PlaygroundMessageRequest
from app.services.playground import TenantNotFoundError, delete_conversation, send_message

router = APIRouter(prefix="/platform-admin/playground", tags=["platform-admin"])

_AGENTS_ERROR_DETAIL = "Não foi possível falar com o agente agora."


@router.post("/messages")
async def send_playground_message_route(
    body: PlaygroundMessageRequest,
    admin: PlatformAdminContext = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> PlaygroundMessageOut:
    try:
        return await send_message(session, body.tenant_id, body.session_id, body.message)
    except TenantNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant não encontrado")
    except (AgentsNetworkError, AgentsApiError):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_AGENTS_ERROR_DETAIL)


@router.delete("/conversations/{tenant_id}/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_playground_conversation_route(
    tenant_id: uuid.UUID,
    session_id: str,
    admin: PlatformAdminContext = Depends(get_current_platform_admin),
) -> None:
    await delete_conversation(tenant_id, session_id)
