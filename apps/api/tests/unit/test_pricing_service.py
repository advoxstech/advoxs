from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.pricing import get_current_pricing_config


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
