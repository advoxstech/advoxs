"""Assinatura padrão pra tenants novos, até a Etapa 2 (Stripe/webhooks de
planos) substituir isso por escolha real de plano no cadastro. Aponta pro
plano "Legado" (sem limite algum) — mesmo comportamento de hoje, sem
regressão pros tenants que se cadastram antes da próxima etapa.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SubscriptionPlan, TenantSubscription


async def build_default_subscription(
    session: AsyncSession, tenant_id: uuid.UUID
) -> TenantSubscription:
    legado = await session.scalar(
        select(SubscriptionPlan).where(SubscriptionPlan.is_legacy.is_(True))
    )
    if legado is None:
        raise RuntimeError("Plano Legado não encontrado — rode a migration 0017")
    return TenantSubscription(id=uuid.uuid4(), tenant_id=tenant_id, plan_id=legado.id)
