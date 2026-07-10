"""Prova, contra um Postgres real, que a RLS bloqueia acesso cross-tenant
pro papel advoxs_app e que advoxs_system continua vendo todos os tenants.

Só roda localmente (precisa de `docker compose up -d postgres` com a
migration 0008 aplicada) — pula com `pytest.skip` se não conseguir
conectar, não entra no CI.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()


async def _consegue_conectar(url: str) -> bool:
    try:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture
async def seeded_tenants():
    """Cria 2 tenants reais + 1 conversa cada, via papel owner (bypassa RLS)."""
    if not await _consegue_conectar(settings.database_url):
        pytest.skip("Postgres real não acessível — rode `docker compose up -d postgres` primeiro")

    owner_engine = create_async_engine(settings.database_url)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, email_contato, credit_balance) "
                "VALUES (:a, 'Tenant A (teste RLS)', 'tenant-a@teste.com', 0), "
                "(:b, 'Tenant B (teste RLS)', 'tenant-b@teste.com', 0)"
            ),
            {"a": TENANT_A, "b": TENANT_B},
        )
        await conn.execute(
            text(
                "INSERT INTO conversations (tenant_id, contact_phone_number, state) "
                "VALUES (:a, '5511900000001', 'agent'), (:b, '5511900000002', 'agent')"
            ),
            {"a": TENANT_A, "b": TENANT_B},
        )

    yield

    async with owner_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM conversations WHERE tenant_id IN (:a, :b)"),
            {"a": TENANT_A, "b": TENANT_B},
        )
        await conn.execute(
            text("DELETE FROM tenants WHERE id IN (:a, :b)"), {"a": TENANT_A, "b": TENANT_B}
        )
    await owner_engine.dispose()


class TestAdvoxsAppRoleEnforcaRLS:
    async def test_ve_so_o_proprio_tenant_sem_filtro_where(self, seeded_tenants) -> None:
        engine = create_async_engine(settings.app_database_url)
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT_A)}
            )
            result = await conn.execute(
                text(
                    "SELECT contact_phone_number FROM conversations WHERE tenant_id IN (:a, :b)"
                ),
                {"a": TENANT_A, "b": TENANT_B},
            )
            rows = [r[0] for r in result.fetchall()]
        await engine.dispose()

        assert rows == ["5511900000001"]

    async def test_insert_com_tenant_id_diferente_e_rejeitado(self, seeded_tenants) -> None:
        engine = create_async_engine(settings.app_database_url)
        with pytest.raises(DBAPIError, match="row-level security"):
            async with engine.begin() as conn:
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT_A)}
                )
                await conn.execute(
                    text(
                        "INSERT INTO conversations (tenant_id, contact_phone_number, state) "
                        "VALUES (:tenant_b, '5511900000099', 'agent')"
                    ),
                    {"tenant_b": TENANT_B},
                )
        await engine.dispose()


class TestAdvoxsSystemRoleBypassaRLS:
    async def test_ve_todos_os_tenants(self, seeded_tenants) -> None:
        engine = create_async_engine(settings.system_database_url)
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT contact_phone_number FROM conversations WHERE tenant_id IN (:a, :b) "
                    "ORDER BY contact_phone_number"
                ),
                {"a": TENANT_A, "b": TENANT_B},
            )
            rows = [r[0] for r in result.fetchall()]
        await engine.dispose()

        assert rows == ["5511900000001", "5511900000002"]
