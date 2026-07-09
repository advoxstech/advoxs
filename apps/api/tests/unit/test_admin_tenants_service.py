import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.admin_tenants import get_tenant_detail, list_tenants

TENANT_ID = uuid.uuid4()
ADMIN_ID = uuid.uuid4()


def _tenant() -> SimpleNamespace:
    return SimpleNamespace(
        id=TENANT_ID,
        name="Escritório Teste",
        email_contato="a@b.com",
        status="active",
        credit_balance=500,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
    )


@pytest.fixture
def session():
    return AsyncMock()


def _execute_result(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


class TestListTenants:
    async def test_marca_whatsapp_conectado_corretamente(self, session) -> None:
        session.execute.side_effect = [
            _execute_result([_tenant()]),
            _execute_result([TENANT_ID]),  # tenant_ids com whatsapp conectado
        ]

        result = await list_tenants(session, limit=50, offset=0)

        assert len(result) == 1
        assert result[0].whatsapp_connected is True


class TestGetTenantDetail:
    async def test_tenant_inexistente_retorna_none(self, session) -> None:
        session.get.return_value = None

        result = await get_tenant_detail(session, TENANT_ID, ADMIN_ID)

        assert result is None
        session.add.assert_not_called()

    async def test_tenant_existente_grava_auditoria_e_retorna_detalhe(self, session) -> None:
        session.get.return_value = _tenant()
        session.execute.side_effect = [_execute_result([]), _execute_result([])]

        result = await get_tenant_detail(session, TENANT_ID, ADMIN_ID)

        assert result is not None
        assert result.name == "Escritório Teste"
        session.add.assert_called_once()
        audit_log = session.add.call_args.args[0]
        assert audit_log.platform_admin_id == ADMIN_ID
        assert audit_log.tenant_id == TENANT_ID
        session.commit.assert_awaited_once()
