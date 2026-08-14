"""Pedido do tenant pra Advoxs configurar a conexão Z-API por ele — ver
app/services/zapi_connection.py pro fluxo manual completo. Este módulo só
gerencia o ciclo de vida do pedido (`zapi_provisioning_requests`); a
provisão em si continua em `provision_zapi_connection`.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ZApiProvisioningRequest


async def get_pending_request(
    session: AsyncSession, tenant_id: uuid.UUID
) -> ZApiProvisioningRequest | None:
    return await session.scalar(
        select(ZApiProvisioningRequest).where(
            ZApiProvisioningRequest.tenant_id == tenant_id,
            ZApiProvisioningRequest.status == "pending",
        )
    )


async def create_or_get_pending_request(
    session: AsyncSession, tenant_id: uuid.UUID
) -> tuple[ZApiProvisioningRequest, bool]:
    """Idempotente — pedir de novo enquanto já existe um pendente devolve o
    mesmo pedido, sem criar duplicado. O segundo valor devolvido (`is_new`)
    distingue os dois casos — usado pelo chamador pra só disparar a
    notificação por e-mail (ver app/services/email_notifications.py) na
    criação de verdade, nunca num pedido repetido."""
    existing = await get_pending_request(session, tenant_id)
    if existing is not None:
        return existing, False
    request = ZApiProvisioningRequest(tenant_id=tenant_id)
    session.add(request)
    await session.commit()
    await session.refresh(request)
    return request, True


async def resolve_pending_request(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Marca qualquer pedido pendente deste tenant como atendido — chamado
    pelo admin depois de provisionar a instância (mesma transação/commit).
    Sem problema se não existir nenhum pedido (admin provisionando por
    contato feito fora do sistema, sem pedido registrado)."""
    request = await get_pending_request(session, tenant_id)
    if request is None:
        return
    request.status = "fulfilled"
    request.resolved_at = datetime.now(UTC)


async def cancel_pending_request(
    session: AsyncSession, tenant_id: uuid.UUID
) -> ZApiProvisioningRequest | None:
    """O próprio tenant desiste do pedido — ação isolada (nenhuma outra
    escrita acontece na mesma chamada), por isso commita sozinha, diferente
    de resolve_pending_request. Devolve None se não havia nada pendente
    (idempotente — cancelar de novo não é erro)."""
    request = await get_pending_request(session, tenant_id)
    if request is None:
        return None
    request.status = "dismissed"
    request.resolved_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(request)
    return request
