"""Testa a migration 0015: schema novo (agents + agent_knowledge_base_files)
e o backfill que clona os 4 agentes fixos pra cada tenant existente.

Só roda localmente (precisa de `docker compose up -d postgres` com a
migration 0015 já aplicada via `alembic upgrade head`) — pula com
`pytest.skip` se não conseguir conectar, mesmo padrão de
`test_rls_isolation.py`. O CI invoca `uv run pytest tests/unit`
explicitamente, então esta pasta nunca é coletada lá.

O arquivo da migration começa com dígito (`0015_...py`) — não é importável
via `import`/`importlib.import_module` normal (cada segmento de um caminho
dotted precisa ser um identificador Python válido). Carregamos o módulo
pelo caminho do arquivo com `importlib.util`, a mesma técnica que o próprio
Alembic usa internamente pra carregar os scripts de revisão.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

_MIGRATION_PATH = (
    Path(__file__).parent.parent.parent / "alembic" / "versions" / "0015_agentes_por_tenant.py"
)
_spec = importlib.util.spec_from_file_location("migration_0015", _MIGRATION_PATH)
migration_0015 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0015)


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
async def owner_engine():
    """Conecta como owner (bypassa RLS) — mesmo papel que o Alembic usa,
    apropriado pra inserir um tenant de teste e depois limpar."""
    if not await _consegue_conectar(settings.database_url):
        pytest.skip("Postgres real não acessível — rode `docker compose up -d postgres` primeiro")
    engine = create_async_engine(settings.database_url)
    yield engine
    await engine.dispose()


async def test_backfill_cria_4_agentes_por_tenant_existente(owner_engine) -> None:
    tenant_id = uuid.uuid4()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, email_contato, status) "
                "VALUES (:id, 'Tenant Teste Migration', 'teste@migration.com', 'active')"
            ),
            {"id": tenant_id},
        )

    # A migration 0015 já rodou (aplicada manualmente via `alembic upgrade
    # head` antes deste teste — ver Step 4) — mas o tenant acima foi criado
    # DEPOIS. Simula o backfill chamando a mesma função que a migration usa,
    # pra provar que ela gera os 4 agentes corretos sem duplicar a lista de
    # agentes fixos aqui no teste — ver Step 3 para essa função.
    async with owner_engine.begin() as conn:
        await conn.execute(migration_0015.build_backfill_insert_statement([tenant_id]))

        result = await conn.execute(
            text("SELECT name, is_entry_point FROM agents WHERE tenant_id = :tid ORDER BY name"),
            {"tid": tenant_id},
        )
        rows = result.all()

    assert len(rows) == 4
    names = {row.name for row in rows}
    assert names == {"Secretária", "Condominial", "Contratos", "Direito do Consumidor"}
    entry_points = [row for row in rows if row.is_entry_point]
    assert len(entry_points) == 1
    assert entry_points[0].name == "Secretária"

    async with owner_engine.begin() as conn:
        await conn.execute(text("DELETE FROM agents WHERE tenant_id = :tid"), {"tid": tenant_id})
        await conn.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})


async def test_indice_unico_parcial_impede_dois_pontos_de_entrada(owner_engine) -> None:
    tenant_id = uuid.uuid4()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, email_contato, status) "
                "VALUES (:id, 'Tenant Teste 2', 'teste2@migration.com', 'active')"
            ),
            {"id": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO agents (id, tenant_id, name, instructions, is_entry_point) "
                "VALUES (gen_random_uuid(), :tid, 'A', 'x', true)"
            ),
            {"tid": tenant_id},
        )

    with pytest.raises(Exception, match="uq_agents_tenant_entry_point|duplicate key"):
        async with owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO agents (id, tenant_id, name, instructions, is_entry_point) "
                    "VALUES (gen_random_uuid(), :tid, 'B', 'y', true)"
                ),
                {"tid": tenant_id},
            )

    async with owner_engine.begin() as conn:
        await conn.execute(text("DELETE FROM agents WHERE tenant_id = :tid"), {"tid": tenant_id})
        await conn.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
