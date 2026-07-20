# Agentes por tenant — Etapa 1 (modelo de dados + CRUD) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar a cada tenant sua própria tabela de agentes de IA (nome + instruções + ponto de entrada) com bases de conhecimento anexáveis, sem tocar ainda no motor de execução (`apps/agents`) — que continua rodando os 4 agentes fixos hoje.

**Architecture:** Duas tabelas novas (`agents`, `agent_knowledge_base_files`) tenant-scoped com RLS, seguindo exatamente o padrão já usado em `tenant_billing_settings`/`end_customer_credit_packages`. Uma migration de dados clona os 4 agentes fixos atuais (prompts hoje em `apps/agents/agents/prompts/*.md`) como linhas próprias de cada tenant existente — zero mudança de comportamento nesta etapa. CRUD REST completo em `/api/v1/agents` + anexação de KB, seguindo o padrão de `/api/v1/end-customer-billing`.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (`apps/api`), mesmo padrão de RLS/tenant-scoping do resto do projeto.

## Global Constraints

- `agents` e `agent_knowledge_base_files` são tenant-scoped, com RLS (`tenant_isolation`, mesmo padrão de todas as outras tabelas tenant-scoped — `USING`/`WITH CHECK` por `current_setting('app.tenant_id', true)::uuid`).
- Exatamente 1 agente por tenant marcado `is_entry_point=true`, garantido por índice único parcial — nunca 0, nunca 2+.
- Um tenant nunca pode ficar sem nenhum agente — apagar o único agente restante é recusado.
- Uma base de conhecimento pode ser anexada a mais de um agente (muitos-pra-muitos) — mas o upload em `/knowledge-base/files` sempre cria o vínculo com exatamente 1 agente (o padrão da UX é "upload é pra um agente"; compartilhar com outro agente depois é uma ação separada via `POST /agents/{id}/knowledge-base-files`).
- Migração de dados: para cada tenant já existente, clona os 4 agentes fixos atuais (secretária + condominial + contratos + direito do consumidor) com o conteúdo *exato* dos arquivos de prompt atuais — sem paráfrase, sem correção de conteúdo, mesmo onde o conteúdo tiver problemas (ex: o prompt de direito do consumidor hoje instrui o agente a negar que é uma IA — isso é clonado como está; corrigir esse conteúdo fica fora do escopo desta etapa).
- **Não tocar `apps/agents` nesta etapa** — o motor de execução continua lendo os arquivos `.md` fixos exatamente como hoje.

Spec de referência: `docs/superpowers/specs/2026-07-20-agentes-customizados-por-tenant-design.md`.

---

### Task 1: Migration — tabelas `agents` + `agent_knowledge_base_files` + backfill

**Files:**
- Create: `apps/api/alembic/versions/0015_agentes_por_tenant.py`
- Test: `apps/api/tests/integration/test_agents_migration.py`

**Interfaces:**
- Consumes: nenhuma dependência de outra task.
- Produces: tabelas `agents` (`id`, `tenant_id`, `name`, `instructions`, `is_entry_point`, `created_at`, `updated_at`) e `agent_knowledge_base_files` (`agent_id`, `knowledge_base_file_id`, `created_at`, PK composta). Após a migration, todo tenant já existente tem exatamente 4 linhas em `agents`, uma com `is_entry_point=true`.

- [ ] **Step 1: Escrever o teste de integração (falhando) contra um Postgres real**

Este projeto testa migrations/RLS contra um Postgres real seguindo o padrão de `apps/api/tests/integration/test_rls_isolation.py` — sem marker (não há `pytest.mark.integration` registrado neste repo; o CI roda só `pytest tests/unit` explicitamente, então a pasta `tests/integration/` nunca é coletada lá), conexão direta via `create_async_engine` (não uma `AsyncSession` de fixture), e um helper `_consegue_conectar` que pula o teste graciosamente (`pytest.skip`) se o Postgres não estiver acessível. Crie `apps/api/tests/integration/test_agents_migration.py` seguindo exatamente esse padrão:

```python
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
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd apps/api && uv run pytest tests/integration/test_agents_migration.py -v`
Expected: FAIL — `relation "agents" does not exist` (a migration ainda não existe) ou erro ao carregar `_MIGRATION_PATH` (arquivo ainda não existe).

- [ ] **Step 3: Escrever a migration**

Crie `apps/api/alembic/versions/0015_agentes_por_tenant.py`. O arquivo tem 3 partes: (a) criação das tabelas + RLS, (b) as 4 constantes de prompt (conteúdo *exato* dos arquivos atuais — ver instrução abaixo), (c) a função de backfill reaproveitável pelo teste.

```python
"""agentes por tenant: tabela agents + anexação de bases de conhecimento

Cada tenant passa a ter os próprios agentes de IA (nome + instruções +
ponto de entrada), em vez dos 4 fixos da plataforma. Esta migration só cria
o modelo de dados e clona os 4 agentes atuais como linhas de cada tenant
existente — o motor de execução (apps/agents) continua lendo os arquivos
.md fixos até a Etapa 2 trocar isso; zero mudança de comportamento aqui.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-20
"""

import uuid

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = ["agents", "agent_knowledge_base_files"]


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column(
            "is_entry_point", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_agents_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
    )
    op.create_index(op.f("ix_agents_tenant_id"), "agents", ["tenant_id"])
    op.create_index(
        op.f("uq_agents_tenant_entry_point"),
        "agents",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_entry_point = true"),
    )

    op.create_table(
        "agent_knowledge_base_files",
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_file_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_knowledge_base_files_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_file_id"],
            ["knowledge_base_files.id"],
            name=op.f(
                "fk_agent_knowledge_base_files_knowledge_base_file_id_knowledge_base_files"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "agent_id", "knowledge_base_file_id", name=op.f("pk_agent_knowledge_base_files")
        ),
    )
    op.create_index(
        op.f("ix_agent_knowledge_base_files_knowledge_base_file_id"),
        "agent_knowledge_base_files",
        ["knowledge_base_file_id"],
    )

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )
    # agent_knowledge_base_files não tem tenant_id próprio (só FKs pra agents
    # e knowledge_base_files, ambas já isoladas por RLS) — a policy acima só
    # roda pra "agents"; a linha abaixo remove agent_knowledge_base_files do
    # ENABLE ROW LEVEL SECURITY genérico e aplica isolamento via join.
    op.execute("ALTER TABLE agent_knowledge_base_files DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON agent_knowledge_base_files")

    bind = op.get_bind()
    tenant_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM tenants")).fetchall()]
    if tenant_ids:
        bind.execute(build_backfill_insert_statement(tenant_ids))


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON agents")
    op.execute("ALTER TABLE agents DISABLE ROW LEVEL SECURITY")
    op.drop_table("agent_knowledge_base_files")
    op.drop_table("agents")


# --- Conteúdo clonado, verbatim, de apps/agents/agents/prompts/*.md ---
# Zero paráfrase/correção — ver Global Constraints do plano sobre por quê.

SECRETARIA_PROMPT = """<COLAR AQUI O CONTEÚDO COMPLETO E EXATO, BYTE POR BYTE, do arquivo apps/agents/agents/prompts/secretaria.md — leia o arquivo primeiro com a ferramenta Read e cole o texto inteiro dentro destas aspas triplas, preservando todos os quebras de linha, acentuação e formatação. Não resuma, não parafraseie, não corrija nada.>"""

CONDOMINIAL_PROMPT = """<COLAR AQUI O CONTEÚDO COMPLETO E EXATO, BYTE POR BYTE, do arquivo apps/agents/agents/prompts/condominial.md — mesma instrução acima.>"""

CONTRATOS_PROMPT = """<COLAR AQUI O CONTEÚDO COMPLETO E EXATO, BYTE POR BYTE, do arquivo apps/agents/agents/prompts/contratos.md — mesma instrução acima.>"""

DIREITO_CONSUMIDOR_PROMPT = """<COLAR AQUI O CONTEÚDO COMPLETO E EXATO, BYTE POR BYTE, do arquivo apps/agents/agents/prompts/direito_consumidor.md — mesma instrução acima. Este arquivo, na linha 14-15, instrui o agente a negar/desviar quando perguntado se é uma IA — clone como está; corrigir esse conteúdo está fora do escopo desta migration.>"""


def build_backfill_insert_statement(tenant_ids: list) -> sa.sql.dml.Insert:
    """Monta o INSERT dos 4 agentes fixos pra cada tenant em `tenant_ids`.
    Função separada (não inline em `upgrade`) pra ser reaproveitada pelo
    teste de integração sem duplicar a lista de agentes."""
    agents_table = sa.table(
        "agents",
        sa.column("id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("instructions", sa.Text()),
        sa.column("is_entry_point", sa.Boolean()),
    )
    rows = []
    for tenant_id in tenant_ids:
        rows.append(
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "name": "Secretária",
                "instructions": SECRETARIA_PROMPT,
                "is_entry_point": True,
            }
        )
        rows.append(
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "name": "Condominial",
                "instructions": CONDOMINIAL_PROMPT,
                "is_entry_point": False,
            }
        )
        rows.append(
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "name": "Contratos",
                "instructions": CONTRATOS_PROMPT,
                "is_entry_point": False,
            }
        )
        rows.append(
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "name": "Direito do Consumidor",
                "instructions": DIREITO_CONSUMIDOR_PROMPT,
                "is_entry_point": False,
            }
        )
    return agents_table.insert().values(rows)
```

**Sobre as 4 constantes de prompt**: são o único ponto deste plano onde o conteúdo não está escrito literalmente aqui — porque copiar ~1000 linhas de texto de prompt pra dentro deste documento duplicaria o conteúdo sem necessidade. A instrução é mecânica e sem ambiguidade: leia cada um dos 4 arquivos em `apps/agents/agents/prompts/` com a ferramenta Read, e cole o conteúdo **completo e exato** de cada um dentro da constante Python correspondente (substituindo o placeholder `<COLAR AQUI...>` inteiro, incluindo as aspas triplas ao redor do texto real). Não resuma, não corrija erros de português do conteúdo original, não remova nada — nem a parte problemática do prompt de direito do consumidor (ver Global Constraints).

- [ ] **Step 4: Rodar a migration localmente e o teste de novo**

Run: `cd apps/api && uv run alembic upgrade head`
Expected: migration `0015` aplica sem erro.

Run: `cd apps/api && uv run pytest tests/integration/test_agents_migration.py -v`
Expected: PASS nos 2 testes.

- [ ] **Step 5: Confirmar visualmente o backfill contra o tenant seed de dev**

Run: `docker compose exec postgres psql -U advoxs -d advoxs -c "SELECT tenant_id, name, is_entry_point FROM agents ORDER BY tenant_id, name;"`
Expected: 4 linhas por tenant já existente no banco de dev, uma delas ("Secretária") com `is_entry_point = t`.

- [ ] **Step 6: Rodar a suíte completa e o lint**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v && uv run ruff check .`
Expected: tudo passa, sem regressão.

- [ ] **Step 7: Commit**

```bash
cd /home/falcao/development/advoxs
git add apps/api/alembic/versions/0015_agentes_por_tenant.py apps/api/tests/integration/test_agents_migration.py
git commit -m "feat(api): migration de agentes por tenant + clona os 4 fixos existentes"
```

---

### Task 2: Modelos SQLAlchemy + schemas Pydantic

**Files:**
- Create: `apps/api/app/models/agent.py`
- Modify: `apps/api/app/models/__init__.py`
- Create: `apps/api/app/schemas/agents.py`

**Interfaces:**
- Consumes: tabelas `agents`/`agent_knowledge_base_files` (Task 1).
- Produces: classes `Agent`, `AgentKnowledgeBaseFile` (ORM) exportadas por `app.models`; schemas `AgentOut`, `AgentCreate`, `AgentUpdate`, `AgentKnowledgeBaseFileOut` em `app.schemas.agents`.

- [ ] **Step 1: Criar o modelo ORM**

Crie `apps/api/app/models/agent.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Agent(Base):
    """Agente de IA próprio do tenant (tenant-scoped) — nome, instruções em
    texto livre e um marcador de ponto de entrada (recebe a primeira
    mensagem de conversas novas; exatamente 1 por tenant)."""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    is_entry_point: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AgentKnowledgeBaseFile(Base):
    """Vínculo muitos-pra-muitos entre um agente e um arquivo da base de
    conhecimento do tenant — só isolado por RLS via join com `agents`
    (ver migration 0015), não tem `tenant_id` próprio."""

    __tablename__ = "agent_knowledge_base_files"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_base_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("knowledge_base_files.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
```

- [ ] **Step 2: Exportar os modelos novos**

Em `apps/api/app/models/__init__.py`, trocar (linhas 1-16):

```python
from app.models.admin_audit_log import AdminAuditLog
from app.models.base import Base
from app.models.billing import CreditPackage, CreditTransaction, PricingConfig
from app.models.conversation import Conversation
from app.models.end_customer_billing import (
    EndCustomerBalance,
    EndCustomerCreditPackage,
    EndCustomerCreditTransaction,
    TenantBillingSettings,
)
from app.models.knowledge_base_file import KnowledgeBaseFile
from app.models.message import Message
from app.models.platform_admin import PlatformAdmin
from app.models.tenant import Tenant
from app.models.user import User
from app.models.whatsapp_number import WhatsAppNumber
```

por:

```python
from app.models.admin_audit_log import AdminAuditLog
from app.models.agent import Agent, AgentKnowledgeBaseFile
from app.models.base import Base
from app.models.billing import CreditPackage, CreditTransaction, PricingConfig
from app.models.conversation import Conversation
from app.models.end_customer_billing import (
    EndCustomerBalance,
    EndCustomerCreditPackage,
    EndCustomerCreditTransaction,
    TenantBillingSettings,
)
from app.models.knowledge_base_file import KnowledgeBaseFile
from app.models.message import Message
from app.models.platform_admin import PlatformAdmin
from app.models.tenant import Tenant
from app.models.user import User
from app.models.whatsapp_number import WhatsAppNumber
```

E trocar o `__all__` (linhas 18-35):

```python
__all__ = [
    "AdminAuditLog",
    "Base",
    "CreditPackage",
    "CreditTransaction",
    "Conversation",
    "EndCustomerBalance",
    "EndCustomerCreditPackage",
    "EndCustomerCreditTransaction",
    "KnowledgeBaseFile",
    "Message",
    "PlatformAdmin",
    "PricingConfig",
    "Tenant",
    "TenantBillingSettings",
    "User",
    "WhatsAppNumber",
]
```

por:

```python
__all__ = [
    "AdminAuditLog",
    "Agent",
    "AgentKnowledgeBaseFile",
    "Base",
    "CreditPackage",
    "CreditTransaction",
    "Conversation",
    "EndCustomerBalance",
    "EndCustomerCreditPackage",
    "EndCustomerCreditTransaction",
    "KnowledgeBaseFile",
    "Message",
    "PlatformAdmin",
    "PricingConfig",
    "Tenant",
    "TenantBillingSettings",
    "User",
    "WhatsAppNumber",
]
```

- [ ] **Step 3: Criar os schemas Pydantic**

Crie `apps/api/app/schemas/agents.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    instructions: str
    is_entry_point: bool
    created_at: datetime
    updated_at: datetime


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1)
    is_entry_point: bool = False


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    instructions: str | None = Field(default=None, min_length=1)
    is_entry_point: bool | None = None


class AgentKnowledgeBaseFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    knowledge_base_file_id: uuid.UUID


class AttachKnowledgeBaseFileIn(BaseModel):
    knowledge_base_file_id: uuid.UUID
```

- [ ] **Step 4: Confirmar que a suíte ainda passa (nenhum teste novo nesta task — só estrutura)**

Run: `cd apps/api && uv run pytest tests/unit -v && uv run ruff check .`
Expected: tudo passa (o modelo/schema novo ainda não é usado por nenhuma rota, então nada quebra nem é exercitado ainda — a Task 3 exercita isso).

- [ ] **Step 5: Commit**

```bash
cd /home/falcao/development/advoxs
git add apps/api/app/models/agent.py apps/api/app/models/__init__.py apps/api/app/schemas/agents.py
git commit -m "feat(api): modelos e schemas de Agent/AgentKnowledgeBaseFile"
```

---

### Task 3: CRUD `/api/v1/agents`

**Files:**
- Create: `apps/api/app/api/v1/agents.py`
- Modify: `apps/api/app/api/v1/router.py`
- Test: `apps/api/tests/unit/test_agents_routes.py`

**Interfaces:**
- Consumes: `Agent` (Task 2), `AgentOut`/`AgentCreate`/`AgentUpdate` (Task 2).
- Produces: rotas `GET /api/v1/agents`, `POST /api/v1/agents`, `PATCH /api/v1/agents/{id}`, `DELETE /api/v1/agents/{id}`.

- [ ] **Step 1: Ler o arquivo de router pra saber onde registrar a rota nova**

Leia `apps/api/app/api/v1/router.py` e identifique o padrão exato usado pra registrar os routers existentes (ex: `end_customer_billing`) — vai precisar dele no Step 3.

- [ ] **Step 2: Escrever os testes (falhando)**

Crie `apps/api/tests/unit/test_agents_routes.py`:

```python
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app

TENANT_ID = uuid.uuid4()
AGENT_ID = uuid.uuid4()


def _agent(name: str = "Secretária", is_entry_point: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=AGENT_ID,
        tenant_id=TENANT_ID,
        name=name,
        instructions="Você é uma secretária.",
        is_entry_point=is_entry_point,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def session():
    mock = AsyncMock()
    mock.add = MagicMock()

    async def fake_refresh(obj):
        obj.created_at = datetime.now(UTC)
        obj.updated_at = datetime.now(UTC)

    mock.refresh.side_effect = fake_refresh
    return mock


@pytest.fixture
def client(session):
    async def override_ctx():
        return TenantContext(user_id=uuid.uuid4(), tenant_id=TENANT_ID, role="admin")

    async def override_session():
        yield session

    app.dependency_overrides[get_current_tenant] = override_ctx
    app.dependency_overrides[get_tenant_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _execute_returning(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/agents")

    assert response.status_code == 401


class TestList:
    def test_lista_agentes_do_tenant(self, client, session) -> None:
        session.execute.return_value = _execute_returning([_agent()])

        response = client.get("/api/v1/agents")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["name"] == "Secretária"
        assert body[0]["is_entry_point"] is True


class TestCreate:
    def test_cria_agente(self, client, session) -> None:
        response = client.post(
            "/api/v1/agents",
            json={"name": "Vendas", "instructions": "Você vende planos.", "is_entry_point": False},
        )

        assert response.status_code == 201
        assert response.json()["name"] == "Vendas"
        session.add.assert_called_once()
        added = session.add.call_args.args[0]
        assert added.tenant_id == TENANT_ID
        session.commit.assert_awaited()

    def test_criar_como_ponto_de_entrada_desmarca_o_anterior(self, client, session) -> None:
        session.execute.return_value = _execute_returning([])

        response = client.post(
            "/api/v1/agents",
            json={"name": "Novo", "instructions": "x", "is_entry_point": True},
        )

        assert response.status_code == 201
        # UPDATE agents SET is_entry_point=false WHERE tenant_id=... roda antes do INSERT.
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        assert any("UPDATE agents" in s for s in statements)


class TestUpdate:
    def test_edita_nome_e_instrucoes(self, client, session) -> None:
        session.scalar.return_value = _agent()

        response = client.patch(
            f"/api/v1/agents/{AGENT_ID}", json={"name": "Secretária Nova"}
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Secretária Nova"

    def test_agente_de_outro_tenant_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.patch(f"/api/v1/agents/{AGENT_ID}", json={"name": "x"})

        assert response.status_code == 404

    def test_marcar_como_ponto_de_entrada_desmarca_o_anterior(self, client, session) -> None:
        session.scalar.return_value = _agent(is_entry_point=False)

        response = client.patch(
            f"/api/v1/agents/{AGENT_ID}", json={"is_entry_point": True}
        )

        assert response.status_code == 200
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        assert any("UPDATE agents" in s for s in statements)


class TestDelete:
    def test_apaga_agente_que_nao_e_ponto_de_entrada(self, client, session) -> None:
        session.scalar.side_effect = [_agent(is_entry_point=False), 2]

        response = client.delete(f"/api/v1/agents/{AGENT_ID}")

        assert response.status_code == 204
        session.delete.assert_awaited_once()

    def test_apagar_ponto_de_entrada_retorna_409(self, client, session) -> None:
        session.scalar.return_value = _agent(is_entry_point=True)

        response = client.delete(f"/api/v1/agents/{AGENT_ID}")

        assert response.status_code == 409
        session.delete.assert_not_awaited()

    def test_apagar_o_unico_agente_retorna_409(self, client, session) -> None:
        session.scalar.side_effect = [_agent(is_entry_point=False), 1]

        response = client.delete(f"/api/v1/agents/{AGENT_ID}")

        assert response.status_code == 409
        session.delete.assert_not_awaited()

    def test_agente_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.delete(f"/api/v1/agents/{AGENT_ID}")

        assert response.status_code == 404
```

- [ ] **Step 3: Rodar os testes e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_routes.py -v`
Expected: FAIL — `404 Not Found` em todas (a rota `/api/v1/agents` ainda não existe).

- [ ] **Step 4: Implementar as rotas**

Crie `apps/api/app/api/v1/agents.py`:

```python
"""CRUD de agentes de IA próprios do tenant."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.models import Agent
from app.schemas.agents import AgentCreate, AgentOut, AgentUpdate

router = APIRouter(prefix="/agents", tags=["agents"])


async def _unset_current_entry_point(ctx: TenantContext, session: AsyncSession) -> None:
    await session.execute(
        update(Agent)
        .where(Agent.tenant_id == ctx.tenant_id, Agent.is_entry_point.is_(True))
        .values(is_entry_point=False)
    )


@router.get("")
async def list_agents(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[AgentOut]:
    result = await session.execute(
        select(Agent).where(Agent.tenant_id == ctx.tenant_id).order_by(Agent.created_at)
    )
    return [AgentOut.model_validate(a) for a in result.scalars().all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentOut:
    if body.is_entry_point:
        await _unset_current_entry_point(ctx, session)

    agent = Agent(tenant_id=ctx.tenant_id, **body.model_dump())
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return AgentOut.model_validate(agent)


async def _get_agent(agent_id: uuid.UUID, ctx: TenantContext, session: AsyncSession) -> Agent:
    agent = await session.scalar(
        select(Agent).where(Agent.id == agent_id, Agent.tenant_id == ctx.tenant_id)
    )
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente não encontrado")
    return agent


@router.patch("/{agent_id}")
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentOut:
    agent = await _get_agent(agent_id, ctx, session)

    if body.is_entry_point is True and not agent.is_entry_point:
        await _unset_current_entry_point(ctx, session)

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(agent, field, value)

    await session.commit()
    await session.refresh(agent)
    return AgentOut.model_validate(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    agent = await _get_agent(agent_id, ctx, session)

    if agent.is_entry_point:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Não é possível apagar o agente ponto de entrada — marque outro agente como ponto de entrada antes",
        )

    total = await session.scalar(
        select(func.count()).select_from(Agent).where(Agent.tenant_id == ctx.tenant_id)
    )
    if total <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="O tenant precisa ter ao menos 1 agente — crie outro antes de apagar este",
        )

    await session.delete(agent)
    await session.commit()
```

- [ ] **Step 5: Registrar o router**

Leia `apps/api/app/api/v1/router.py` e adicione o import e o `include_router` de `agents`, seguindo exatamente o mesmo padrão já usado para os outros routers do arquivo (import `from app.api.v1.agents import router as agents_router` ou o nome de variável que o padrão local usar, e a chamada `api_router.include_router(agents_router)` na mesma lista dos demais).

- [ ] **Step 6: Rodar os testes e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_routes.py -v`
Expected: PASS nos 11 testes.

- [ ] **Step 7: Rodar a suíte completa e o lint**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v && uv run ruff check .`
Expected: tudo passa, sem regressão.

- [ ] **Step 8: Commit**

```bash
cd /home/falcao/development/advoxs
git add apps/api/app/api/v1/agents.py apps/api/app/api/v1/router.py apps/api/tests/unit/test_agents_routes.py
git commit -m "feat(api): CRUD de agentes por tenant em /api/v1/agents"
```

---

### Task 4: Anexação de bases de conhecimento a um agente

**Files:**
- Modify: `apps/api/app/api/v1/agents.py`
- Test: `apps/api/tests/unit/test_agents_routes.py`

**Interfaces:**
- Consumes: `AgentKnowledgeBaseFile` (Task 2), `_get_agent` (Task 3, já no mesmo módulo).
- Produces: `POST /api/v1/agents/{id}/knowledge-base-files`, `DELETE /api/v1/agents/{id}/knowledge-base-files/{file_id}`.

- [ ] **Step 1: Escrever os testes (falhando)**

Adicione ao final de `apps/api/tests/unit/test_agents_routes.py` (depois da classe `TestDelete`, mantendo o padrão de fixtures já definido no topo do arquivo):

```python


class TestAttachKnowledgeBaseFile:
    def test_anexa_arquivo_existente(self, client, session) -> None:
        session.scalar.side_effect = [_agent(), SimpleNamespace(id=uuid.uuid4())]

        response = client.post(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files",
            json={"knowledge_base_file_id": str(uuid.uuid4())},
        )

        assert response.status_code == 201
        session.add.assert_called_once()
        session.commit.assert_awaited()

    def test_agente_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files",
            json={"knowledge_base_file_id": str(uuid.uuid4())},
        )

        assert response.status_code == 404

    def test_arquivo_de_outro_tenant_retorna_404(self, client, session) -> None:
        session.scalar.side_effect = [_agent(), None]

        response = client.post(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files",
            json={"knowledge_base_file_id": str(uuid.uuid4())},
        )

        assert response.status_code == 404


class TestDetachKnowledgeBaseFile:
    def test_desanexa_arquivo(self, client, session) -> None:
        session.scalar.return_value = _agent()
        link = SimpleNamespace(agent_id=AGENT_ID, knowledge_base_file_id=uuid.uuid4())
        session.get = AsyncMock(return_value=link)

        response = client.delete(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files/{link.knowledge_base_file_id}"
        )

        assert response.status_code == 204
        session.delete.assert_awaited_once_with(link)

    def test_vinculo_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = _agent()
        session.get = AsyncMock(return_value=None)

        response = client.delete(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files/{uuid.uuid4()}"
        )

        assert response.status_code == 404
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_routes.py::TestAttachKnowledgeBaseFile tests/unit/test_agents_routes.py::TestDetachKnowledgeBaseFile -v`
Expected: FAIL — `404 Not Found` em todos (as rotas ainda não existem).

- [ ] **Step 3: Implementar**

Em `apps/api/app/api/v1/agents.py`, trocar a linha de import dos models e schemas (topo do arquivo):

```python
from app.models import Agent
from app.schemas.agents import AgentCreate, AgentOut, AgentUpdate
```

por:

```python
from app.models import Agent, AgentKnowledgeBaseFile, KnowledgeBaseFile
from app.schemas.agents import (
    AgentCreate,
    AgentKnowledgeBaseFileOut,
    AgentOut,
    AgentUpdate,
    AttachKnowledgeBaseFileIn,
)
```

E adicionar ao final do arquivo (depois de `delete_agent`):

```python


@router.post("/{agent_id}/knowledge-base-files", status_code=status.HTTP_201_CREATED)
async def attach_knowledge_base_file(
    agent_id: uuid.UUID,
    body: AttachKnowledgeBaseFileIn,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentKnowledgeBaseFileOut:
    await _get_agent(agent_id, ctx, session)

    file = await session.scalar(
        select(KnowledgeBaseFile).where(
            KnowledgeBaseFile.id == body.knowledge_base_file_id,
            KnowledgeBaseFile.tenant_id == ctx.tenant_id,
        )
    )
    if file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Arquivo não encontrado"
        )

    link = AgentKnowledgeBaseFile(agent_id=agent_id, knowledge_base_file_id=file.id)
    session.add(link)
    await session.commit()
    return AgentKnowledgeBaseFileOut.model_validate(link)


@router.delete(
    "/{agent_id}/knowledge-base-files/{file_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def detach_knowledge_base_file(
    agent_id: uuid.UUID,
    file_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    await _get_agent(agent_id, ctx, session)

    link = await session.get(AgentKnowledgeBaseFile, (agent_id, file_id))
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vínculo não encontrado")

    await session.delete(link)
    await session.commit()
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_routes.py -v`
Expected: PASS em todos os 16 testes do arquivo.

- [ ] **Step 5: Rodar a suíte completa e o lint**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v && uv run ruff check .`
Expected: tudo passa.

- [ ] **Step 6: Commit**

```bash
cd /home/falcao/development/advoxs
git add apps/api/app/api/v1/agents.py apps/api/tests/unit/test_agents_routes.py
git commit -m "feat(api): anexar/desanexar base de conhecimento a um agente"
```

---

### Task 5: `agent_id` obrigatório no upload de `/knowledge-base/files`

**Files:**
- Modify: `apps/api/app/api/v1/knowledge_base.py`
- Test: `apps/api/tests/unit/test_knowledge_base_routes.py`

**Interfaces:**
- Consumes: `Agent`, `AgentKnowledgeBaseFile` (Task 2/3).
- Produces: `POST /knowledge-base/files` agora exige o campo de formulário `agent_id`; cria o vínculo em `agent_knowledge_base_files` na mesma transação do upload.

- [ ] **Step 1: Escrever os testes (falhando)**

Em `apps/api/tests/unit/test_knowledge_base_routes.py`, adicionar ao topo do arquivo (junto aos outros imports) `import uuid` já está presente; adicione também:

```python
from app.models import Agent
```

E trocar a função `_upload` (linhas 72-76 do arquivo original):

```python
def _upload(client, filename="regimento.pdf", content=b"%PDF-1.4 conteudo", mime="application/pdf"):
    return client.post(
        "/api/v1/knowledge-base/files",
        files={"file": (filename, io.BytesIO(content), mime)},
    )
```

por:

```python
AGENT_ID = uuid.uuid4()


def _upload(
    client,
    filename="regimento.pdf",
    content=b"%PDF-1.4 conteudo",
    mime="application/pdf",
    agent_id=None,
):
    return client.post(
        "/api/v1/knowledge-base/files",
        files={"file": (filename, io.BytesIO(content), mime)},
        data={"agent_id": str(agent_id or AGENT_ID)},
    )
```

E trocar o teste `test_upload_feliz_enfileira_apos_commit` (dentro de `class TestUpload`):

```python
    def test_upload_feliz_enfileira_apos_commit(self, client, session, arq, tmp_path) -> None:
        # 1ª scalar: soma do storage usado; 2ª: checagem de duplicado.
        session.scalar.side_effect = [0, None]

        response = _upload(client)
```

por:

```python
    def test_upload_feliz_enfileira_apos_commit(self, client, session, arq, tmp_path) -> None:
        # 1ª scalar: agente-destino válido; 2ª: soma do storage usado; 3ª: checagem de duplicado.
        session.scalar.side_effect = [
            SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID),
            0,
            None,
        ]

        response = _upload(client)
```

E adicionar, ao final da classe `TestUpload` (mantendo o padrão dos testes já existentes), um teste novo:

```python

    def test_upload_sem_agent_id_retorna_422(self, client) -> None:
        response = client.post(
            "/api/v1/knowledge-base/files",
            files={"file": ("regimento.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )

        assert response.status_code == 422

    def test_upload_com_agente_de_outro_tenant_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = _upload(client)

        assert response.status_code == 404
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_knowledge_base_routes.py -v`
Expected: FAIL em `test_upload_feliz_enfileira_apos_commit` (a rota ainda não pede `agent_id`, a sequência de `session.scalar.side_effect` não corresponde ao que o código atual chama) e em `test_upload_com_agente_de_outro_tenant_retorna_404` (ainda não existe a checagem). `test_upload_sem_agent_id_retorna_422` também falha (campo ainda não é obrigatório).

- [ ] **Step 3: Implementar**

Em `apps/api/app/api/v1/knowledge_base.py`, trocar o import de models (linha 17):

```python
from app.models import KnowledgeBaseFile
```

por:

```python
from app.models import Agent, AgentKnowledgeBaseFile, KnowledgeBaseFile
```

Adicionar `Form` ao import do FastAPI (linha 8):

```python
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
```

por:

```python
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
```

Trocar a assinatura de `upload_file` (linhas 34-40):

```python
@router.post("/files", status_code=status.HTTP_202_ACCEPTED)
async def upload_file(
    file: UploadFile = File(...),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
    arq: ArqRedis = Depends(get_arq_pool),
) -> KnowledgeBaseFileOut:
```

por:

```python
@router.post("/files", status_code=status.HTTP_202_ACCEPTED)
async def upload_file(
    file: UploadFile = File(...),
    agent_id: uuid.UUID = Form(...),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
    arq: ArqRedis = Depends(get_arq_pool),
) -> KnowledgeBaseFileOut:
```

Adicionar a checagem do agente-destino logo no início do corpo da função, antes da validação de extensão (imediatamente depois da linha `) -> KnowledgeBaseFileOut:`, antes de `filename = file.filename or ""`):

```python
    agent = await session.scalar(
        select(Agent).where(Agent.id == agent_id, Agent.tenant_id == ctx.tenant_id)
    )
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente não encontrado")

```

Por fim, criar o vínculo na mesma transação do registro do arquivo — trocar (linhas 91-99 do arquivo original):

```python
    record = KnowledgeBaseFile(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        filename=filename,
        size_bytes=len(data),
        mime_type=expected_mime,
        status="processing",
    )
    session.add(record)
```

por:

```python
    record = KnowledgeBaseFile(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        filename=filename,
        size_bytes=len(data),
        mime_type=expected_mime,
        status="processing",
    )
    session.add(record)
    session.add(AgentKnowledgeBaseFile(agent_id=agent_id, knowledge_base_file_id=record.id))
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_knowledge_base_routes.py -v`
Expected: PASS em todos os testes do arquivo.

- [ ] **Step 5: Rodar a suíte completa e o lint**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v && uv run ruff check .`
Expected: tudo passa, sem regressão.

- [ ] **Step 6: Commit**

```bash
cd /home/falcao/development/advoxs
git add apps/api/app/api/v1/knowledge_base.py apps/api/tests/unit/test_knowledge_base_routes.py
git commit -m "feat(api): upload de base de conhecimento passa a exigir agent_id"
```

---

## Verificação final

- [ ] **Step 1: Rodar a suíte completa do `api` do zero**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v && uv run ruff check .`
Expected: tudo verde.

- [ ] **Step 2: Teste manual rápido (dev local)**

Com o stack local no ar, rodar a migration (`docker compose exec api uv run alembic upgrade head` ou reiniciar o container), confirmar via `psql` que os tenants de dev já têm os 4 agentes clonados, e exercitar `GET /api/v1/agents` autenticado (via `curl` com um token de teste) pra confirmar a lista.

## Nota para a Etapa 2

O `apps/agents` service continua, depois desta etapa, lendo os arquivos `.md` fixos — nada muda no comportamento em produção. A Etapa 2 (plano separado, a escrever depois desta ser implementada e revisada) é quem troca o motor de execução pra ler da tabela `agents` recém-criada e generaliza o grafo do LangGraph pra 2 nós (`agent_node` + `tool_node`), conforme o spec.
