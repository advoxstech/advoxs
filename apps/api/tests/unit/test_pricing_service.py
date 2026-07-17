from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.pricing import calcular_creditos, get_current_pricing_config

CONFIG = SimpleNamespace(
    tokens_per_credit=1000, input_weight=Decimal("0.3"), output_weight=Decimal("1.0")
)


def test_pondera_input_e_output():
    # 2000*0.3 + 500*1.0 = 1100 tokens ponderados -> 1.1 créditos
    assert calcular_creditos(2000, 500, 2500, CONFIG) == Decimal("1.1000")


def test_arredonda_para_4_casas_half_up():
    assert calcular_creditos(1, 0, 1, CONFIG) == Decimal("0.0003")
    assert calcular_creditos(166, 0, 166, CONFIG) == Decimal("0.0498")


def test_fallback_sem_breakdown_trata_tudo_como_output():
    assert calcular_creditos(0, 0, 3500, CONFIG) == Decimal("3.5000")


def test_zero_tokens_zero_creditos():
    assert calcular_creditos(0, 0, 0, CONFIG) == Decimal("0.0000")


async def test_retorna_a_config_vigente():
    config = SimpleNamespace(
        tokens_per_credit=1000, input_weight=Decimal("0.3"), output_weight=Decimal("1.0")
    )
    session = AsyncMock()
    session.scalar.return_value = config

    result = await get_current_pricing_config(session)

    assert result is config
    session.scalar.assert_awaited_once()


async def test_sem_config_levanta_runtime_error():
    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(RuntimeError, match="pricing_config"):
        await get_current_pricing_config(session)
