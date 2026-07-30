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
                30000,  # openai_tokens_input
                15000,  # openai_tokens_output
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
        assert result.openai_cost_estimate_usd == Decimal("0.0375")
        assert result.low_balance_tenants[0].name == "Escritório Baixo"
        assert result.whatsapp_connected.connected == 3
        assert result.whatsapp_connected.total == 42
        assert result.knowledge_base_usage.total_files == 12
        assert result.knowledge_base_usage.total_size_bytes == 204800

    async def test_sem_consumo_estimativa_de_custo_e_zero(self, session) -> None:
        session.scalar = AsyncMock(
            side_effect=[
                0,  # tenants_total
                Decimal("0"),  # revenue_brl_last_30_days
                0,  # sold
                0,  # consumed_negative
                0,  # messages_processed
                0,  # agent_executions
                0,  # tokens_consumed
                0,  # openai_tokens_input
                0,  # openai_tokens_output
                0,  # whatsapp_connected
                0,  # kb_files
                0,  # kb_bytes
            ]
        )
        session.execute = AsyncMock(
            side_effect=[_execute_result([]), _execute_result([]), _execute_result([])]
        )

        result = await build_dashboard(session)

        assert result.openai_cost_estimate_usd == Decimal("0")
