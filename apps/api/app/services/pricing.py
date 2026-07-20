"""Leitura da config global de pricing (pesos input/output, tokens por crédito).

A tabela é versionada: a config vigente é a de `effective_at` mais recente já
alcançado. A migration 0013 seeda a inicial — ausência de config é erro de
deploy, não estado válido.
"""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PricingConfig

_PRECISION = Decimal("1")


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


def calcular_creditos(
    tokens_input: int, tokens_output: int, tokens_used: int, config: PricingConfig
) -> Decimal:
    """Créditos inteiros (arredondado pro mais próximo, HALF_UP) a partir dos
    tokens ponderados. Consumo muito barato pode arredondar pra 0 créditos —
    decisão deliberada, sem mínimo de 1 crédito por cobrança.

    Espelha apps/worker/app/pricing.py (codebases separados, mesmo padrão da
    antiga env duplicada). Fallback de transição: breakdown zerado com
    tokens_used > 0 (agents antigo) trata tudo como output — cobra a mais,
    nunca a menos."""
    if not tokens_input and not tokens_output and tokens_used:
        tokens_output = tokens_used
    ponderados = (
        Decimal(tokens_input) * config.input_weight
        + Decimal(tokens_output) * config.output_weight
    )
    return (ponderados / Decimal(config.tokens_per_credit)).quantize(
        _PRECISION, rounding=ROUND_HALF_UP
    )
