"""Envio/limpeza de conversas do playground de agentes (admin) — efêmero:
nada é persistido no Postgres do `api`, a memória vive só no checkpoint do
LangGraph (dentro do agents service)."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.agents import delete_agent_checkpoint, send_playground_message
from app.models import Tenant
from app.schemas.playground import PlaygroundMessageOut
from app.services.agents_engine import load_agents_for_engine


class TenantNotFoundError(Exception):
    pass


async def send_message(
    session: AsyncSession, tenant_id: uuid.UUID, session_id: str, message: str
) -> PlaygroundMessageOut:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise TenantNotFoundError()

    agents = await load_agents_for_engine(session, tenant_id)

    result = await send_playground_message(
        tenant_id=str(tenant_id),
        contact_phone_number=f"playground-{session_id}",
        message=message,
        agents=agents,
    )

    if result is None:
        return PlaygroundMessageOut(
            responses=[], tokens_used=None, current_agent=None, grouped=True
        )

    return PlaygroundMessageOut(
        responses=result["responses"],
        tokens_used=result["tokens_used"],
        current_agent=result["current_agent"],
        grouped=False,
    )


async def delete_conversation(tenant_id: uuid.UUID, session_id: str) -> None:
    await delete_agent_checkpoint(f"{tenant_id}:playground-{session_id}")
