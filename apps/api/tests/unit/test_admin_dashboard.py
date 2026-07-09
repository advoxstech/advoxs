import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.admin_dashboard import build_dashboard

TENANT_ID = uuid.uuid4()


def _execute_result(rows: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


@pytest.fixture
def session():
    return AsyncMock()


class TestBuildDashboard:
    async def test_monta_o_snapshot_com_os_valores_agregados(self, session) -> None:
        session.scalar = AsyncMock(
            side_effect=[
                42,  # tenants_total
                Decimal("350.00"),  # revenue_brl_last_30_days
                5000,  # sold
                -1200,  # consumed_negative
                987,  # messages_processed
                310,  # agent_executions
                45000,  # tokens_consumed
                3,  # whatsapp_connected
                12,  # kb_files
                204800,  # kb_bytes
            ]
        )
        session.execute = AsyncMock(
            side_effect=[
                _execute_result([("active", 40), ("suspended", 2)]),
                _execute_result([(date(2026, 7, 1), 2), (date(2026, 7, 2), 1)]),
                _execute_result([(TENANT_ID, "Escritório Baixo", 10)]),
            ]
        )

        result = await build_dashboard(session)

        assert result.tenants_total == 42
        assert result.tenants_by_status.active == 40
        assert result.tenants_by_status.suspended == 2
        assert len(result.new_tenants_last_30_days) == 2
        assert result.new_tenants_last_30_days[0].count == 2
        assert result.revenue_brl_last_30_days == Decimal("350.00")
        assert result.credits_summary.sold == 5000
        assert result.credits_summary.consumed == 1200  # abs() do valor negativo
        assert result.messages_processed == 987
        assert result.agent_executions == 310
        assert result.tokens_consumed == 45000
        assert result.low_balance_tenants[0].name == "Escritório Baixo"
        assert result.whatsapp_connected.connected == 3
        assert result.whatsapp_connected.total == 42
        assert result.knowledge_base_usage.total_files == 12
        assert result.knowledge_base_usage.total_size_bytes == 204800
