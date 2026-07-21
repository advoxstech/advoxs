import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.subscriptions import get_active_subscription

TENANT_ID = uuid.uuid4()


async def test_retorna_assinatura_e_plano_vigentes():
    subscription = object()
    plan = object()
    session = AsyncMock()
    result = MagicMock()
    result.one_or_none.return_value = (subscription, plan)
    session.execute.return_value = result

    got_subscription, got_plan = await get_active_subscription(session, TENANT_ID)

    assert got_subscription is subscription
    assert got_plan is plan


async def test_sem_assinatura_levanta_runtime_error():
    session = AsyncMock()
    result = MagicMock()
    result.one_or_none.return_value = None
    session.execute.return_value = result

    with pytest.raises(RuntimeError, match="tenant_subscriptions"):
        await get_active_subscription(session, TENANT_ID)
