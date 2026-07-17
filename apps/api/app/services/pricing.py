"""Leitura da config global de pricing (pesos input/output, tokens por crédito).

A tabela é versionada: a config vigente é a de `effective_at` mais recente já
alcançado. A migration 0013 seeda a inicial — ausência de config é erro de
deploy, não estado válido.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PricingConfig


async def get_current_pricing_config(session: AsyncSession) -> PricingConfig:
    config = await session.scalar(
        select(PricingConfig)
        .where(PricingConfig.effective_at <= datetime.now(UTC))
        .order_by(PricingConfig.effective_at.desc())
        .limit(1)
    )
    if config is None:
        raise RuntimeError(
            "Nenhuma pricing_config vigente — rode as migrations (0013 seeda a inicial)"
        )
    return config
