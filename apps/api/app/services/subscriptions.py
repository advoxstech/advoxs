"""Leitura da assinatura vigente de um tenant (plano + estado no Stripe).

Toda tenant_subscriptions é criada por uma migration (backfill pro plano
Legado, ver 0017) ou por app/services/default_subscription.py (cadastro
novo) — ausência é erro de deploy/dado corrompido, não estado válido (mesmo
princípio de app/services/pricing.py::get_current_pricing_config).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SubscriptionPlan, TenantSubscription


async def get_active_subscription(
    session: AsyncSession, tenant_id: uuid.UUID
) -> tuple[TenantSubscription, SubscriptionPlan]:
    result = await session.execute(
        select(TenantSubscription, SubscriptionPlan)
        .join(SubscriptionPlan, TenantSubscription.plan_id == SubscriptionPlan.id)
        .where(TenantSubscription.tenant_id == tenant_id)
    )
    row = result.one_or_none()
    if row is None:
        raise RuntimeError(
            f"Tenant {tenant_id} sem tenant_subscriptions — rode a migration de backfill (0017) "
            "ou confira app/services/default_subscription.py"
        )
    return row
