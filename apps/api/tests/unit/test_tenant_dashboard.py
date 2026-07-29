import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.dashboard import build_tenant_dashboard

TENANT_ID = uuid.uuid4()


def _recent(n: int = 2) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=uuid.uuid4(),
            contact_phone_number=f"551199999000{i}",
            state="agent",
            last_message_at=datetime(2026, 7, 8, 12, 0, tzinfo=UTC),
        )
        for i in range(n)
    ]


def _execute_result(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


@pytest.fixture
def session():
    return AsyncMock()


class TestBuildTenantDashboard:
    async def test_monta_o_snapshot_com_os_valores_agregados(self, session) -> None:
        session.scalar = AsyncMock(
            side_effect=[
                1500,  # credit_balance
                "5511987654321",  # display_phone_number (conectado)
                12,  # conversations_total
                3,  # waiting_human
                87,  # agent_messages (30d)
                -240,  # credits_consumed (negativo no ledger)
                5,  # kb_ready
                1,  # kb_error
                False,  # billing_enabled
                "standalone",  # billing_provider
                None,  # stripe_account_id
            ]
        )
        session.execute = AsyncMock(return_value=_execute_result(_recent(2)))

        result = await build_tenant_dashboard(session, TENANT_ID)

        assert result.credit_balance == 1500
        assert result.whatsapp.connected is True
        assert result.whatsapp.display_phone_number == "551 **** 4321"  # mascarado
        assert result.conversations.total == 12
        assert result.conversations.waiting_human == 3
        assert result.usage_last_30_days.agent_messages == 87
        assert result.usage_last_30_days.credits_consumed == 240  # abs()
        assert result.knowledge_base.ready == 5
        assert result.knowledge_base.error == 1
        assert result.end_customer_earnings.connected is False
        assert result.end_customer_earnings.total_brl is None
        assert len(result.recent_conversations) == 2

    async def test_sem_whatsapp_conectado_retorna_disconnected(self, session) -> None:
        session.scalar = AsyncMock(
            side_effect=[0, None, 0, 0, 0, 0, 0, 0, False, "standalone", None]
        )
        session.execute = AsyncMock(return_value=_execute_result([]))

        result = await build_tenant_dashboard(session, TENANT_ID)

        assert result.whatsapp.connected is False
        assert result.whatsapp.display_phone_number is None
        assert result.recent_conversations == []

    async def test_todas_as_queries_filtram_por_tenant(self, session) -> None:
        """Isolamento: nenhuma query do dashboard pode esquecer o filtro de
        tenant — mesma classe de bug do vazamento corrigido em billing/status."""
        session.scalar = AsyncMock(
            side_effect=[0, None, 0, 0, 0, 0, 0, 0, False, "standalone", None]
        )
        session.execute = AsyncMock(return_value=_execute_result([]))

        await build_tenant_dashboard(session, TENANT_ID)

        # A 1ª query filtra por tenants.id (a PK do próprio tenant); todas as
        # demais filtram pela coluna tenant_id das tabelas tenant-scoped.
        scalar_sqls = [str(call.args[0]) for call in session.scalar.await_args_list]
        assert "tenants.id" in scalar_sqls[0]
        for sql in scalar_sqls[1:]:
            assert "tenant_id" in sql
        execute_sql = str(session.execute.await_args_list[0].args[0])
        assert "WHERE conversations.tenant_id" in execute_sql

    async def test_end_customer_earnings_soma_compra_avulsa_e_assinatura_quando_conectado(
        self, session
    ) -> None:
        session.scalar = AsyncMock(
            side_effect=[
                0,  # credit_balance
                None,  # display_phone_number
                0,  # conversations_total
                0,  # waiting_human
                0,  # agent_messages (30d)
                0,  # credits_consumed
                0,  # kb_ready
                0,  # kb_error
                True,  # billing_enabled
                "connect",  # billing_provider
                "acct_123",  # stripe_account_id
                150,  # purchases_total (soma de price_brl)
                50,  # subscription_total (soma de amount_brl)
            ]
        )
        session.execute = AsyncMock(return_value=_execute_result([]))

        result = await build_tenant_dashboard(session, TENANT_ID)

        assert result.end_customer_earnings.connected is True
        assert result.end_customer_earnings.total_brl == 200

    async def test_end_customer_earnings_desconectado_quando_billing_desabilitado(
        self, session
    ) -> None:
        """billing_provider="connect" com conta configurada mas billing
        desabilitado ainda mostra "sem conta conectada" — evita a tile levar
        pra um /configuracoes/cobranca-clientes sem a aba Faturamento visível."""
        session.scalar = AsyncMock(
            side_effect=[0, None, 0, 0, 0, 0, 0, 0, False, "connect", "acct_123"]
        )
        session.execute = AsyncMock(return_value=_execute_result([]))

        result = await build_tenant_dashboard(session, TENANT_ID)

        assert result.end_customer_earnings.connected is False
        assert result.end_customer_earnings.total_brl is None
