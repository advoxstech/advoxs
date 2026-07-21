# Planos de Assinatura — Etapa 1 (modelo de dados + enforcement) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modelar planos de assinatura (agentes/ferramentas-reservado/KB/créditos mensais) e um estado de assinatura por tenant no `apps/api`, migrar todo tenant já existente pro plano "Legado" (sem limite algum — zero regressão), garantir que todo tenant NOVO (via o cadastro self-service atual, ainda baseado em pacote de crédito) também recebe uma assinatura por padrão, e aplicar os limites de agentes/KB nas duas rotas de criação relevantes.

**Architecture:** Duas tabelas novas (`subscription_plans` global, `tenant_subscriptions` tenant-scoped 1:1), um serviço de leitura (`get_active_subscription`, espelhando o padrão já usado por `get_current_pricing_config`) consumido por dois pontos de enforcement (`POST /api/v1/agents`, `POST /api/v1/knowledge-base/files`). **Esta etapa não toca Stripe nem o fluxo de cadastro público** — o cadastro continua vendendo pacotes de crédito exatamente como hoje; só ganha, na mesma transação, uma assinatura padrão no plano Legado (mesmo comportamento de hoje: sem limite), até a Etapa 2 (Stripe/webhooks de planos reais) substituir isso por escolha de plano de verdade. Ver `docs/superpowers/specs/2026-07-21-planos-assinatura-design.md` para o desenho completo (inclui as etapas futuras, fora do escopo daqui).

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (`apps/api`) — mesma stack já em uso, sem dependências novas.

## Global Constraints

- Toda coluna de teto (`max_agents`, `max_extra_tools`, `max_knowledge_base_files`, `max_knowledge_base_storage_bytes`) usa **`NULL` = sem limite** — nunca um número mágico grande. Só o plano "Legado" usa `NULL` em todas.
- **Toda checagem de limite sempre executa a mesma query, independente do valor do teto** — a comparação (`>=`/`>`) é o único ponto condicional, nunca a query em si. Isso evita o problema de alinhamento posicional de mock já resolvido em etapas anteriores deste projeto (mocks `session.scalar.side_effect`/`session.execute.side_effect` continuam determinísticos independente dos dados do plano).
- `get_active_subscription` **levanta `RuntimeError`** quando não encontra `tenant_subscriptions` pro tenant — mesmo princípio de `get_current_pricing_config` ("ausência é erro de deploy, não estado válido"). Por isso a Task 2 (assinatura padrão pra tenant novo) é obrigatória nesta mesma etapa — sem ela, todo cadastro novo quebraria `POST /api/v1/agents`/`POST /knowledge-base/files` até a Etapa 2 existir.
- **`max_extra_tools` não tem nenhum ponto de enforcement nesta etapa** — a coluna existe, nada mais. Não existe hoje nenhuma tool extra pra contar.
- **Nenhuma mudança em Stripe, nenhuma mudança no fluxo de cadastro público, nenhuma tela nova em `apps/web`** — tudo isso é Etapa 2/3, fora do escopo deste plano.
- Toda tabela tenant-scoped nova segue exatamente o padrão de RLS já estabelecido (migration 0008 já dá `GRANT` automático via `ALTER DEFAULT PRIVILEGES` pra migrations novas — não repetir `GRANT` manual).

---

### Task 1: Modelo de dados — `subscription_plans` + `tenant_subscriptions`

**Files:**
- Create: `apps/api/app/models/subscription.py`
- Modify: `apps/api/app/models/__init__.py`
- Create: `apps/api/alembic/versions/0017_planos_assinatura.py`
- Create: `apps/api/app/services/subscriptions.py`
- Create: `apps/api/tests/unit/test_subscriptions_service.py`

**Interfaces:**
- Consumes: nada de outra task deste plano (task independente, primeira da sequência).
- Produces: `SubscriptionPlan`, `TenantSubscription` (models); `get_active_subscription(session, tenant_id) -> tuple[TenantSubscription, SubscriptionPlan]` — consumido pelas Tasks 2, 3 e 4.

- [ ] **Step 1: Criar os models**

Criar `apps/api/app/models/subscription.py`:

```python
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SubscriptionPlan(Base):
    """Plano de assinatura mensal (global) — define tetos de agentes, base de
    conhecimento e ferramentas extras (reservado, sem enforcement ainda),
    mais um bônus de créditos concedido a cada ciclo pago. `NULL` num teto
    significa sem limite — usado só pelo plano "Legado" (nunca vendido,
    migra tenants já existentes sem regressão)."""

    __tablename__ = "subscription_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    price_brl: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    max_agents: Mapped[int | None] = mapped_column(Integer)
    max_extra_tools: Mapped[int | None] = mapped_column(Integer)
    max_knowledge_base_files: Mapped[int | None] = mapped_column(Integer)
    max_knowledge_base_storage_bytes: Mapped[int | None] = mapped_column(BigInteger)
    monthly_credits_granted: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    is_legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class TenantSubscription(Base):
    """Assinatura vigente de um tenant (tenant-scoped, 1:1) — aponta pro
    plano atual e espelha o status/ciclo da assinatura no Stripe.
    `stripe_subscription_id` é `NULL` só pra tenants no plano Legado (sem
    assinatura Stripe de verdade — Postgres permite múltiplos `NULL` numa
    coluna `UNIQUE`, então isso nunca colide entre tenants)."""

    __tablename__ = "tenant_subscriptions"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'past_due', 'canceled')", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, unique=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("subscription_plans.id"), nullable=False
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'active'"))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
```

Em `apps/api/app/models/__init__.py`, adicionar o import (depois da linha `from app.models.platform_admin import PlatformAdmin`, antes de `from app.models.tenant import Tenant`):

```python
from app.models.subscription import SubscriptionPlan, TenantSubscription
```

E adicionar `"SubscriptionPlan"` e `"TenantSubscription"` ao `__all__` (depois de `"PricingConfig"`, antes de `"Tenant"`):

```python
    "SubscriptionPlan",
    "Tenant",
    "TenantBillingSettings",
    "TenantSubscription",
```

(reorganizando essas 4 linhas em ordem alfabética exata nesse trecho do `__all__`.)

- [ ] **Step 2: Criar a migration**

Criar `apps/api/alembic/versions/0017_planos_assinatura.py`:

```python
"""planos de assinatura (agentes, ferramentas reservadas, KB, créditos mensais)

Introduz subscription_plans (global) e tenant_subscriptions (tenant-scoped,
1:1) — ver docs/superpowers/specs/2026-07-21-planos-assinatura-design.md.
Backfill: todo tenant já existente ganha uma tenant_subscriptions apontando
pro plano "Legado" (sem limite algum, price_brl=0) — preserva o
comportamento de hoje (sem teto de agentes/KB) pra quem já é cliente; só
cadastros novos, a partir deste deploy, ganham uma assinatura de verdade
(ver app/services/default_subscription.py).

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-21
"""

import uuid

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = ["tenant_subscriptions"]

subscription_plans = sa.table(
    "subscription_plans",
    sa.column("id", sa.Uuid()),
    sa.column("name", sa.String()),
    sa.column("price_brl", sa.Numeric(10, 2)),
    sa.column("max_agents", sa.Integer()),
    sa.column("max_extra_tools", sa.Integer()),
    sa.column("max_knowledge_base_files", sa.Integer()),
    sa.column("max_knowledge_base_storage_bytes", sa.BigInteger()),
    sa.column("monthly_credits_granted", sa.Integer()),
    sa.column("is_legacy", sa.Boolean()),
    sa.column("active", sa.Boolean()),
)

ESSENCIAL_ID = uuid.uuid4()
PROFISSIONAL_ID = uuid.uuid4()
COMPLETO_ID = uuid.uuid4()
LEGADO_ID = uuid.uuid4()

MB = 1024 * 1024

PLANS = [
    {
        "id": ESSENCIAL_ID,
        "name": "Essencial",
        "price_brl": "97.00",
        "max_agents": 5,
        "max_extra_tools": 0,
        "max_knowledge_base_files": 50,
        "max_knowledge_base_storage_bytes": 250 * MB,
        "monthly_credits_granted": 300,
        "is_legacy": False,
        "active": True,
    },
    {
        "id": PROFISSIONAL_ID,
        "name": "Profissional",
        "price_brl": "247.00",
        "max_agents": 12,
        "max_extra_tools": 3,
        "max_knowledge_base_files": 150,
        "max_knowledge_base_storage_bytes": 750 * MB,
        "monthly_credits_granted": 1000,
        "is_legacy": False,
        "active": True,
    },
    {
        "id": COMPLETO_ID,
        "name": "Escritório Completo",
        "price_brl": "497.00",
        "max_agents": 30,
        "max_extra_tools": 8,
        "max_knowledge_base_files": 400,
        "max_knowledge_base_storage_bytes": 1536 * MB,
        "monthly_credits_granted": 3000,
        "is_legacy": False,
        "active": True,
    },
    {
        "id": LEGADO_ID,
        "name": "Legado",
        "price_brl": "0.00",
        "max_agents": None,
        "max_extra_tools": None,
        "max_knowledge_base_files": None,
        "max_knowledge_base_storage_bytes": None,
        "monthly_credits_granted": 0,
        "is_legacy": True,
        "active": True,
    },
]


def upgrade() -> None:
    op.create_table(
        "subscription_plans",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("price_brl", sa.Numeric(10, 2), nullable=False),
        sa.Column("max_agents", sa.Integer(), nullable=True),
        sa.Column("max_extra_tools", sa.Integer(), nullable=True),
        sa.Column("max_knowledge_base_files", sa.Integer(), nullable=True),
        sa.Column("max_knowledge_base_storage_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "monthly_credits_granted", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("is_legacy", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscription_plans")),
    )

    op.create_table(
        "tenant_subscriptions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('active', 'past_due', 'canceled')",
            name=op.f("ck_tenant_subscriptions_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_subscriptions_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["subscription_plans.id"],
            name=op.f("fk_tenant_subscriptions_plan_id_subscription_plans"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_subscriptions")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_tenant_subscriptions_tenant_id")),
        sa.UniqueConstraint(
            "stripe_subscription_id", name=op.f("uq_tenant_subscriptions_stripe_subscription_id")
        ),
    )
    op.create_index(
        op.f("ix_tenant_subscriptions_tenant_id"), "tenant_subscriptions", ["tenant_id"]
    )

    op.bulk_insert(subscription_plans, PLANS)

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )

    op.execute(
        "INSERT INTO tenant_subscriptions (id, tenant_id, plan_id, status) "
        f"SELECT gen_random_uuid(), id, '{LEGADO_ID}', 'active' FROM tenants"
    )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("tenant_subscriptions")
    op.drop_table("subscription_plans")
```

- [ ] **Step 3: Verificar a migration**

Se houver um Postgres real disponível neste ambiente (confira com `docker compose ps postgres` ou equivalente): rode `cd apps/api && uv run alembic upgrade head` e depois `uv run alembic downgrade -1 && uv run alembic upgrade head` de novo pra confirmar que sobe/desce/sobe limpo. Se não houver Postgres real disponível, valide só a sintaxe:

```bash
python3 -c "import ast; ast.parse(open('apps/api/alembic/versions/0017_planos_assinatura.py').read())"
```

Expected: sem erro (ou, com Postgres real, as 3 tabelas de plano seedadas — confira com `SELECT name, is_legacy FROM subscription_plans;` — e uma linha em `tenant_subscriptions` por tenant já existente).

- [ ] **Step 4: Escrever o teste que falha para `get_active_subscription`**

Criar `apps/api/tests/unit/test_subscriptions_service.py`:

```python
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
```

- [ ] **Step 5: Rodar e confirmar falha**

Run: `cd apps/api && uv run pytest tests/unit/test_subscriptions_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.subscriptions'`.

- [ ] **Step 6: Implementar `get_active_subscription`**

Criar `apps/api/app/services/subscriptions.py`:

```python
"""Leitura da assinatura vigente de um tenant (plano + estado no Stripe).

Toda tenant_subscriptions é criada por uma migration (backfill pro plano
Legado, ver 0017) ou por app/services/default_subscription.py (cadastro
novo) — ausência é erro de deploy/dado corrompido, não estado válido (mesmo
princípio de app/services/pricing.py::get_current_pricing_config).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SubscriptionPlan, TenantSubscription


async def get_active_subscription(
    session: AsyncSession, tenant_id: uuid.UUID
) -> tuple[TenantSubscription, SubscriptionPlan]:
    result = await session.execute(
        select(TenantSubscription, SubscriptionPlan)
        .join(SubscriptionPlan, TenantSubscription.plan_id == SubscriptionPlan.id)
        .where(TenantSubscription.tenant_id == tenant_id)
    )
    row = result.one_or_none()
    if row is None:
        raise RuntimeError(
            f"Tenant {tenant_id} sem tenant_subscriptions — rode a migration de backfill (0017) "
            "ou confira app/services/default_subscription.py"
        )
    return row
```

- [ ] **Step 7: Rodar e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_subscriptions_service.py -v`
Expected: os 2 testes passam.

- [ ] **Step 8: Rodar a suíte completa + lint**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v && uv run ruff check .`
Expected: todos passam (ou skip nos de integração que exigem Postgres real), lint limpo. Nenhum teste existente deveria mudar de resultado nesta task — só arquivos novos.

- [ ] **Step 9: Commit**

```bash
git add apps/api/app/models/subscription.py apps/api/app/models/__init__.py apps/api/alembic/versions/0017_planos_assinatura.py apps/api/app/services/subscriptions.py apps/api/tests/unit/test_subscriptions_service.py
git commit -m "feat(api): modelo de dados de planos de assinatura + get_active_subscription"
```

---

### Task 2: Assinatura padrão (Legado) pra tenant novo no cadastro atual

**Files:**
- Create: `apps/api/app/services/default_subscription.py`
- Modify: `apps/api/app/services/billing.py`
- Modify: `apps/api/scripts/seed_dev.py`
- Test: `apps/api/tests/unit/test_billing_service.py`

**Interfaces:**
- Consumes: `SubscriptionPlan`, `TenantSubscription` (Task 1).
- Produces: `build_default_subscription(session, tenant_id) -> TenantSubscription` (async — precisa buscar o id do plano Legado) — consumido só dentro desta task (`_process_signup`, `seed_dev.py`); nenhuma task futura deste plano depende dele.

- [ ] **Step 1: Escrever o teste que falha**

Em `apps/api/tests/unit/test_billing_service.py`, os testes que passam pelo corpo de `_process_signup` (cria tenant/user/agentes) hoje usam `session.scalar.return_value = None` (valor único pra toda chamada de `session.scalar`). Como `_process_signup` vai ganhar UMA chamada nova a `session.scalar` (buscar o plano Legado, depois da chamada já existente de dedup), esses testes precisam de `session.scalar.side_effect = [None, <plano Legado>]` no lugar do `.return_value = None`.

Substituir, em `apps/api/tests/unit/test_billing_service.py`:

1. `test_cria_tenant_user_e_transacao` (dentro de `class TestProcessCheckoutCompleted`) — troque a primeira linha do corpo e o `assert len(added) == 7`:

```python
    async def test_cria_tenant_user_e_transacao(self, session) -> None:
        session.scalar.side_effect = [None, SimpleNamespace(id=uuid.uuid4(), is_legacy=True)]
        session.get.return_value = _package()
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

        session.flush = AsyncMock(side_effect=fake_flush)

        await process_checkout_completed(session, self._stripe_session())

        # tenant + user + transaction + os 4 agentes padrão + a assinatura
        # padrão (plano Legado) — ver default_subscription.py.
        assert len(added) == 8
        tenant, user, transaction = added[:3]
        assert tenant.name == "Escritório Teste"
        assert tenant.credit_balance == 2750
        assert user.email == "a@b.com"
        assert user.password_hash == "hash-fake"
        assert user.role == "admin"
        assert user.tenant_id == tenant.id
        assert transaction.amount_credits == 2750
        assert transaction.stripe_payment_id == "cs_123"
        session.commit.assert_awaited_once()
```

2. `test_cria_4_agentes_padrao_para_o_tenant_novo` — só a primeira linha do corpo:

```python
        session.scalar.side_effect = [None, SimpleNamespace(id=uuid.uuid4(), is_legacy=True)]
```

3. `test_cria_tenant_com_stripe_session_real_nao_dict` — primeira linha do corpo + `assert len(added) == 7` → `8`:

```python
        session.scalar.side_effect = [None, SimpleNamespace(id=uuid.uuid4(), is_legacy=True)]
```
```python
        assert len(added) == 8
```

4. `test_integrity_error_no_commit_e_tratado` — primeira linha do corpo:

```python
        session.scalar.side_effect = [None, SimpleNamespace(id=uuid.uuid4(), is_legacy=True)]
```

5. `test_signup_gera_token_de_auto_login` — primeira linha do corpo:

```python
        session.scalar.side_effect = [None, SimpleNamespace(id=uuid.uuid4(), is_legacy=True)]
```

6. `test_falha_no_redis_nao_quebra_o_webhook` — primeira linha do corpo:

```python
        session.scalar.side_effect = [None, SimpleNamespace(id=uuid.uuid4(), is_legacy=True)]
```

Os demais testes do arquivo (`test_metadata_incompleta_nao_processa`, `test_pacote_nao_encontrado_nao_processa`, `test_credit_package_id_malformado_nao_processa`, todo `TestProcessCheckoutCompletedRecompra`, `test_recompra_nao_gera_token`) retornam antes de chegar no provisionamento de agentes/assinatura (ou são o fluxo de recompra, que não cria assinatura nova) — **não precisam de nenhuma mudança**.

Adicionar, no fim de `class TestProcessCheckoutCompleted`, um teste novo:

```python
    async def test_cria_assinatura_legado_para_o_tenant_novo(self, session) -> None:
        """Até a Etapa 2 (Stripe/planos) substituir por escolha real de plano
        no cadastro, todo tenant novo recebe uma tenant_subscriptions
        apontando pro plano Legado — sem isso, POST /api/v1/agents e
        /knowledge-base/files quebrariam (RuntimeError de
        get_active_subscription) pra todo tenant criado nessa janela."""
        legado_plan = SimpleNamespace(id=uuid.uuid4(), is_legacy=True)
        session.scalar.side_effect = [None, legado_plan]
        session.get.return_value = _package()
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

        session.flush = AsyncMock(side_effect=fake_flush)

        await process_checkout_completed(session, self._stripe_session())

        tenant = added[0]
        subscriptions_added = [obj for obj in added if type(obj).__name__ == "TenantSubscription"]
        assert len(subscriptions_added) == 1
        assert subscriptions_added[0].tenant_id == tenant.id
        assert subscriptions_added[0].plan_id == legado_plan.id
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `cd apps/api && uv run pytest tests/unit/test_billing_service.py -v`
Expected: FAIL nos 7 testes tocados/novos — os 6 com `side_effect` novo falham porque a 2ª chamada de `session.scalar` ainda não existe no código real (o `side_effect` list vira irrelevante, o `session.scalar` real só é chamado 1 vez, então o `None` da 1ª posição é usado normalmente e nada quebra por causa disso — mas os `assert len(added) == 8` falham com `7`, e o teste novo falha porque nenhuma `TenantSubscription` é criada ainda).

- [ ] **Step 3: Implementar `build_default_subscription`**

Criar `apps/api/app/services/default_subscription.py`:

```python
"""Assinatura padrão pra tenants novos, até a Etapa 2 (Stripe/webhooks de
planos) substituir isso por escolha real de plano no cadastro. Aponta pro
plano "Legado" (sem limite algum) — mesmo comportamento de hoje, sem
regressão pros tenants que se cadastram antes da próxima etapa.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SubscriptionPlan, TenantSubscription


async def build_default_subscription(
    session: AsyncSession, tenant_id: uuid.UUID
) -> TenantSubscription:
    legado = await session.scalar(
        select(SubscriptionPlan).where(SubscriptionPlan.is_legacy.is_(True))
    )
    if legado is None:
        raise RuntimeError("Plano Legado não encontrado — rode a migration 0017")
    return TenantSubscription(id=uuid.uuid4(), tenant_id=tenant_id, plan_id=legado.id)
```

Em `apps/api/app/services/billing.py`, adicionar o import (entre `from app.services.default_agents import build_default_agents` e `from app.services.signup_tokens import store_login_token`):

```python
from app.services.default_subscription import build_default_subscription
```

E, em `_process_signup`, adicionar a linha logo depois do loop `for agent in build_default_agents(tenant.id): session.add(agent)` (antes do `try: await session.commit()`):

```python
    # Mesma transação do tenant/user/transação — sem isso, o tenant novo
    # nasce sem assinatura, e POST /api/v1/agents e /knowledge-base/files
    # quebram (RuntimeError de get_active_subscription) até a Etapa 2
    # substituir isso por escolha real de plano no cadastro.
    session.add(await build_default_subscription(session, tenant.id))
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_billing_service.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 5: Atualizar `seed_dev.py`**

Em `apps/api/scripts/seed_dev.py`, adicionar o import (junto a `from app.services.default_agents import build_default_agents`):

```python
from app.services.default_subscription import build_default_subscription
```

E, no corpo de `seed()`, adicionar a linha logo depois do loop de agentes (antes do `print(f"Tenant {tenant.id} + usuário {args.email} criados (com os 4 agentes padrão).")`):

```python
            session.add(await build_default_subscription(session, tenant.id))
```

- [ ] **Step 6: Rodar a suíte completa + lint**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v && uv run ruff check .`
Expected: todos passam (ou skip nos de integração que exigem Postgres real), lint limpo.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/default_subscription.py apps/api/app/services/billing.py apps/api/scripts/seed_dev.py apps/api/tests/unit/test_billing_service.py
git commit -m "feat(api): assinatura padrão (plano Legado) pra todo tenant novo no cadastro atual"
```

---

### Task 3: Enforcement de limite de agentes (`POST /api/v1/agents`)

**Files:**
- Modify: `apps/api/app/api/v1/agents.py`
- Test: `apps/api/tests/unit/test_agents_routes.py`

**Interfaces:**
- Consumes: `get_active_subscription` (Task 1).
- Produces: nada novo pras tasks seguintes deste plano (Task 4 é independente, mexe num arquivo diferente).

- [ ] **Step 1: Escrever os testes que falham**

Em `apps/api/tests/unit/test_agents_routes.py`, adicionar (depois do import de `AsyncMock, MagicMock` — sem novo import necessário) um helper no topo do arquivo, logo depois da função `_agent`:

```python
def _active_subscription(plan_overrides: dict | None = None, subscription_overrides: dict | None = None) -> MagicMock:
    plan = SimpleNamespace(
        id=uuid.uuid4(),
        name="Profissional",
        max_agents=None,
        max_extra_tools=None,
        max_knowledge_base_files=None,
        max_knowledge_base_storage_bytes=None,
        monthly_credits_granted=1000,
        is_legacy=False,
        active=True,
        **(plan_overrides or {}),
    )
    subscription = SimpleNamespace(status="active", **(subscription_overrides or {}))
    result = MagicMock()
    result.one_or_none.return_value = (subscription, plan)
    return result
```

Atualizar `test_cria_agente` (adicionar a 1ª linha do corpo):

```python
    def test_cria_agente(self, client, session) -> None:
        session.execute.return_value = _active_subscription()

        response = client.post(
            "/api/v1/agents",
            json={"name": "Vendas", "instructions": "Você vende planos.", "is_entry_point": False},
        )
```

(o resto do teste continua igual.)

Atualizar `test_criar_como_ponto_de_entrada_desmarca_o_anterior` (troca `.return_value` por `.side_effect`, já que agora há 2 chamadas de `session.execute`: a minha nova, depois a de `_unset_current_entry_point`):

```python
    def test_criar_como_ponto_de_entrada_desmarca_o_anterior(self, client, session) -> None:
        session.execute.side_effect = [_active_subscription(), _execute_returning([])]

        response = client.post(
            "/api/v1/agents",
            json={"name": "Novo", "instructions": "x", "is_entry_point": True},
        )

        assert response.status_code == 201
        # UPDATE agents SET is_entry_point=false WHERE tenant_id=... roda antes do INSERT.
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        assert any("UPDATE agents" in s for s in statements)
```

Adicionar, no fim de `class TestCreate`, 2 testes novos:

```python
    def test_limite_de_agentes_do_plano_retorna_409(self, client, session) -> None:
        session.execute.return_value = _active_subscription({"max_agents": 2})
        session.scalar.return_value = 2

        response = client.post(
            "/api/v1/agents",
            json={"name": "Novo", "instructions": "x", "is_entry_point": False},
        )

        assert response.status_code == 409
        assert "agentes" in response.json()["detail"].lower()

    def test_assinatura_inativa_retorna_409(self, client, session) -> None:
        session.execute.return_value = _active_subscription(
            subscription_overrides={"status": "past_due"}
        )

        response = client.post(
            "/api/v1/agents",
            json={"name": "Novo", "instructions": "x", "is_entry_point": False},
        )

        assert response.status_code == 409
        assert "assinatura" in response.json()["detail"].lower()
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_routes.py -v`
Expected: FAIL — `test_cria_agente` continua passando por acidente (nenhuma chamada nova ainda existe no código, então o `session.execute.return_value` configurado não é sequer usado, mas também não quebra nada); os 2 testes novos falham porque `create_agent` ainda não checa assinatura/limite (sempre `201`, nunca `409`); rode especificamente os 2 novos pra confirmar:

Run: `cd apps/api && uv run pytest tests/unit/test_agents_routes.py -v -k "limite_de_agentes or assinatura_inativa"`
Expected: FAIL nos 2, `assert 201 == 409`.

- [ ] **Step 3: Implementar o enforcement**

Em `apps/api/app/api/v1/agents.py`, adicionar o import (junto aos demais `from app...`):

```python
from app.services.subscriptions import get_active_subscription
```

E substituir o corpo de `create_agent`:

```python
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentOut:
    subscription, plan = await get_active_subscription(session, ctx.tenant_id)
    if subscription.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sua assinatura não está ativa — regularize o pagamento para continuar",
        )

    total = await session.scalar(
        select(func.count()).select_from(Agent).where(Agent.tenant_id == ctx.tenant_id)
    )
    if plan.max_agents is not None and total >= plan.max_agents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Seu plano atual ({plan.name}) permite até {plan.max_agents} agentes — "
                "faça upgrade para criar mais"
            ),
        )

    if body.is_entry_point:
        await _unset_current_entry_point(ctx, session)

    agent = Agent(id=uuid.uuid4(), tenant_id=ctx.tenant_id, **body.model_dump())
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return AgentOut.model_validate(agent)
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_routes.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 5: Rodar a suíte completa + lint**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v && uv run ruff check .`
Expected: todos passam (ou skip), lint limpo.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/api/v1/agents.py apps/api/tests/unit/test_agents_routes.py
git commit -m "feat(api): aplica o limite de agentes do plano em POST /api/v1/agents"
```

---

### Task 4: Enforcement de limite de KB (`POST /api/v1/knowledge-base/files`)

**Files:**
- Modify: `apps/api/app/api/v1/knowledge_base.py`
- Modify: `apps/api/app/core/config.py`
- Test: `apps/api/tests/unit/test_knowledge_base_routes.py`

**Interfaces:**
- Consumes: `get_active_subscription` (Task 1).
- Produces: nada — última task de código deste plano.

- [ ] **Step 1: Escrever os testes que falham**

Em `apps/api/tests/unit/test_knowledge_base_routes.py`, adicionar um helper no topo do arquivo, logo depois da função `_record`:

```python
def _active_subscription(plan_overrides: dict | None = None, subscription_overrides: dict | None = None) -> MagicMock:
    plan = SimpleNamespace(
        id=uuid.uuid4(),
        name="Profissional",
        max_agents=None,
        max_extra_tools=None,
        max_knowledge_base_files=None,
        max_knowledge_base_storage_bytes=None,
        monthly_credits_granted=1000,
        is_legacy=False,
        active=True,
        **(plan_overrides or {}),
    )
    subscription = SimpleNamespace(status="active", **(subscription_overrides or {}))
    result = MagicMock()
    result.one_or_none.return_value = (subscription, plan)
    return result
```

Atualizar `class TestUpload`, uma por uma:

1. `test_upload_feliz_enfileira_apos_commit` — adicionar `session.execute.return_value = _active_subscription()` como 1ª linha do corpo, e inserir `0` (novo `file_count`) como 3º item do `session.scalar.side_effect`:

```python
    def test_upload_feliz_enfileira_apos_commit(self, client, session, arq, tmp_path) -> None:
        session.execute.return_value = _active_subscription()
        # 1ª scalar: agente-destino válido; 2ª: soma do storage usado;
        # 3ª: contagem de arquivos; 4ª: checagem de duplicado.
        session.scalar.side_effect = [
            SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID),
            0,
            0,
            None,
        ]
```

(o resto do teste continua igual.)

2. `test_arquivo_vazio_400`:

```python
    def test_arquivo_vazio_400(self, client, session) -> None:
        session.execute.return_value = _active_subscription()
        # 1ª scalar: agente-destino válido; 2ª: soma do storage usado; 3ª: contagem de arquivos.
        session.scalar.side_effect = [SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID), 0, 0]

        response = _upload(client, content=b"")

        assert response.status_code == 400
```

3. `test_storage_estourado_413` — o limite de storage passa a vir do plano, não mais do env `kb_max_total_size_bytes` — troque o `monkeypatch.setattr` por um override no plano mockado, e remova o parâmetro `monkeypatch` da assinatura se não for mais usado em nenhuma outra linha do teste (confira antes de remover):

```python
    def test_storage_estourado_413(self, client, session) -> None:
        session.execute.return_value = _active_subscription({"max_knowledge_base_storage_bytes": 100})
        # 1ª scalar: agente-destino válido; 2ª: soma do storage usado.
        session.scalar.side_effect = [SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID), 95]

        response = _upload(client, content=b"x" * 10)

        assert response.status_code == 413
        assert "restam" in response.json()["detail"]
```

4. `test_nome_duplicado_409`:

```python
    def test_nome_duplicado_409(self, client, session) -> None:
        session.execute.return_value = _active_subscription()
        # 1ª scalar: agente-destino válido; 2ª: soma do storage usado;
        # 3ª: contagem de arquivos; 4ª: checagem de duplicado.
        session.scalar.side_effect = [
            SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID), 0, 0, FILE_ID
        ]

        response = _upload(client)

        assert response.status_code == 409
```

5. `test_corrida_de_duplicado_no_commit_409`:

```python
    def test_corrida_de_duplicado_no_commit_409(self, client, session, tmp_path) -> None:
        session.execute.return_value = _active_subscription()
        # Dois uploads concorrentes passam pelo check de duplicado; a unique
        # constraint (tenant_id, filename) estoura no commit do segundo.
        # 1ª scalar: agente-destino válido; 2ª: soma do storage usado;
        # 3ª: contagem de arquivos; 4ª: checagem de duplicado.
        session.scalar.side_effect = [
            SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID), 0, 0, None
        ]
        session.commit.side_effect = IntegrityError("stmt", {}, Exception("uq"))

        response = _upload(client)

        assert response.status_code == 409
        tenant_dir = tmp_path / str(TENANT_ID)
        assert not tenant_dir.exists() or not any(tenant_dir.iterdir())
```

6. `test_upload_com_agente_de_outro_tenant_retorna_404` e `test_upload_sem_agent_id_sem_ponto_de_entrada_retorna_500` — **sem mudança nenhuma** (ambos retornam 404/500 na resolução do agente, antes de qualquer checagem de assinatura/plano).

7. `test_upload_sem_agent_id_usa_agente_ponto_de_entrada_do_tenant`:

```python
    def test_upload_sem_agent_id_usa_agente_ponto_de_entrada_do_tenant(
        self, client, session
    ) -> None:
        session.execute.return_value = _active_subscription()
        entry_point_id = uuid.uuid4()
        session.scalar.side_effect = [
            SimpleNamespace(id=entry_point_id, tenant_id=TENANT_ID, is_entry_point=True),
            0,
            0,
            None,
        ]

        response = client.post(
            "/api/v1/knowledge-base/files",
            files={"file": ("regimento.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )

        assert response.status_code == 202
        link_calls = [
            call.args[0]
            for call in session.add.call_args_list
            if type(call.args[0]).__name__ == "AgentKnowledgeBaseFile"
        ]
        assert len(link_calls) == 1
        assert link_calls[0].agent_id == entry_point_id
```

Adicionar, no fim de `class TestUpload`, 2 testes novos:

```python
    def test_limite_de_arquivos_do_plano_retorna_409(self, client, session) -> None:
        session.execute.return_value = _active_subscription({"max_knowledge_base_files": 2})
        # 1ª scalar: agente-destino válido; 2ª: soma do storage usado; 3ª: contagem de arquivos (no teto).
        session.scalar.side_effect = [SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID), 0, 2]

        response = _upload(client)

        assert response.status_code == 409
        assert "arquivos" in response.json()["detail"].lower()

    def test_assinatura_inativa_retorna_409(self, client, session) -> None:
        session.execute.return_value = _active_subscription(
            subscription_overrides={"status": "past_due"}
        )
        session.scalar.side_effect = [SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID)]

        response = _upload(client)

        assert response.status_code == 409
        assert "assinatura" in response.json()["detail"].lower()
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `cd apps/api && uv run pytest tests/unit/test_knowledge_base_routes.py -v`
Expected: FAIL nos testes tocados — os que tiveram `session.scalar.side_effect` com um item novo inserido (`test_arquivo_vazio_400`, `test_nome_duplicado_409`, `test_corrida_de_duplicado_no_commit_409`, `test_upload_sem_agent_id_...`) agora recebem um valor errado na posição errada assim que o código real também mudar — mas ANTES da Step 3 (implementação), o código real ainda só faz 2 chamadas de `scalar`, então o item extra do teste fica sem uso e a asserção de status pode até passar por acidente em alguns; a forma confiável de confirmar RED é rodar os 2 testes novos:

Run: `cd apps/api && uv run pytest tests/unit/test_knowledge_base_routes.py -v -k "limite_de_arquivos or assinatura_inativa"`
Expected: FAIL nos 2 (sempre `202`/`400`, nunca `409`).

- [ ] **Step 3: Implementar o enforcement**

Em `apps/api/app/api/v1/knowledge_base.py`, adicionar o import (junto aos demais `from app...`):

```python
from app.services.subscriptions import get_active_subscription
```

Adicionar, logo depois do bloco de resolução do agente (depois da linha `agent_id = agent.id` do `else`, antes de `filename = file.filename or ""`):

```python
    subscription, plan = await get_active_subscription(session, ctx.tenant_id)
    if subscription.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sua assinatura não está ativa — regularize o pagamento para continuar",
        )
```

Substituir o bloco de checagem de storage (as linhas de `used = await session.scalar(...)` até o `raise HTTPException` do `413`) por:

```python
    used = await session.scalar(
        select(func.coalesce(func.sum(KnowledgeBaseFile.size_bytes), 0)).where(
            KnowledgeBaseFile.tenant_id == ctx.tenant_id
        )
    )
    if (
        plan.max_knowledge_base_storage_bytes is not None
        and used + len(data) > plan.max_knowledge_base_storage_bytes
    ):
        remaining = max(plan.max_knowledge_base_storage_bytes - used, 0)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Limite de storage do escritório atingido — restam {remaining} bytes",
        )

    file_count = await session.scalar(
        select(func.count())
        .select_from(KnowledgeBaseFile)
        .where(KnowledgeBaseFile.tenant_id == ctx.tenant_id)
    )
    if plan.max_knowledge_base_files is not None and file_count >= plan.max_knowledge_base_files:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Seu plano atual ({plan.name}) permite até {plan.max_knowledge_base_files} "
                "arquivos — faça upgrade para enviar mais"
            ),
        )
```

Em `apps/api/app/core/config.py`, remover a linha (agora morta — o teto de storage vem do plano, não mais de um env global):

```python
    kb_max_total_size_bytes: int = 500 * 1024 * 1024
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_knowledge_base_routes.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 5: Rodar a suíte completa + lint**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v && uv run ruff check .`
Expected: todos passam (ou skip), lint limpo — confirme especialmente que `ruff check` não aponta `settings.kb_max_total_size_bytes` esquecido em nenhum outro arquivo (já confirmado no design que só `config.py`/`knowledge_base.py`/o teste usavam essa env).

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/api/v1/knowledge_base.py apps/api/app/core/config.py apps/api/tests/unit/test_knowledge_base_routes.py
git commit -m "feat(api): aplica os limites de storage e contagem de arquivos do plano no upload de KB"
```

---

### Task 5: Documentação — `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: comportamento final das Tasks 1-4 (deve ser a última task).
- Produces: nenhum código — só documentação, sem passo de teste.

- [ ] **Step 1: Adicionar as tabelas novas na seção "Modelo de Dados"**

Na seção `## Modelo de Dados (Postgres)`, adicionar (depois de `### end_customer_credit_transactions`, antes de `### Relacionamentos (resumo)`):

```markdown
### `subscription_plans` (global)
- `id` (uuid, PK)
- `name` (string — "Essencial", "Profissional", "Escritório Completo", "Legado")
- `price_brl` (numeric)
- `max_agents` (integer, nullable = sem limite)
- `max_extra_tools` (integer, nullable — reservado, sem enforcement)
- `max_knowledge_base_files` (integer, nullable = sem limite)
- `max_knowledge_base_storage_bytes` (bigint, nullable = sem limite)
- `monthly_credits_granted` (integer, default `0`)
- `is_legacy` (bool, default `false`) — marca o plano de migração, nunca vendido
- `active` (bool, default `true`)
- `created_at`, `updated_at`

### `tenant_subscriptions` (tenant-scoped, 1:1 com tenant)
- `id` (uuid, PK)
- `tenant_id` (FK → `tenants`, `UNIQUE`)
- `plan_id` (FK → `subscription_plans`)
- `stripe_subscription_id` (nullable, `UNIQUE` — `NULL` só pros tenants no plano Legado)
- `status` (`active` | `past_due` | `canceled`)
- `current_period_end` (nullable — `NULL` pros tenants Legado, que nunca expiram)
- `created_at`, `updated_at`
```

Atualizar o bloco `### Relacionamentos (resumo)` adicionando:

```
tenants 1───1 tenant_subscriptions
tenant_subscriptions N───1 subscription_plans
```

- [ ] **Step 2: Adicionar a seção "Planos de Assinatura"**

Adicionar uma seção nova, logo depois de `## Billing / Créditos` (antes de `## Integração WhatsApp Business`):

```markdown
## Planos de Assinatura — ✅ modelo de dados + enforcement implementados (Etapa 1)

> Segunda dimensão de billing, independente da wallet de créditos acima — governa quantos **agentes**, **arquivos de base de conhecimento** e (reservado pro futuro) **ferramentas extras** cada tenant pode ter. Ver `docs/superpowers/specs/2026-07-21-planos-assinatura-design.md` pro desenho completo.

- **3 planos públicos + 1 plano "Legado"** (`subscription_plans`, seedados na migration `0017`): Essencial (R$ 97/mês, até 5 agentes, até 50 arquivos de KB/250MB, 300 créditos/mês), Profissional (R$ 247, até 12 agentes, até 150 arquivos/750MB, 1.000 créditos/mês), Escritório Completo (R$ 497, até 30 agentes, até 400 arquivos/1,5GB, 3.000 créditos/mês). Números sujeitos a reajuste — a estrutura (5 dimensões escalando junto) é o que importa. "Legado" tem todo teto `NULL` (sem limite) — nunca aparece pra venda, só migra tenants já existentes e provisiona tenants novos até a Etapa 2 existir (ver abaixo).
- **`get_active_subscription`** (`app/services/subscriptions.py`): resolve a assinatura + plano vigente de um tenant — levanta `RuntimeError` se não encontrar (ausência é erro de deploy, mesmo princípio de `get_current_pricing_config`).
- **Enforcement em 2 pontos**: `POST /api/v1/agents` (limite de `max_agents`, contagem fresca a cada chamada) e `POST /api/v1/knowledge-base/files` (limite de `max_knowledge_base_files` por contagem + `max_knowledge_base_storage_bytes` — substituiu o antigo env global `KB_MAX_TOTAL_SIZE_BYTES`, removido). Os dois também recusam (`409`) quando `tenant_subscriptions.status != "active"`.
- **`max_extra_tools` é só reservado** — nenhuma tool extra existe ainda, zero enforcement.
- ✅ **Todo tenant já existente foi migrado pro plano Legado** (backfill na migration `0017`) — zero regressão, ninguém foi bloqueado retroativamente por já ter mais agentes/arquivos do que os planos públicos permitiriam.
- ✅ **Todo tenant NOVO** (via o cadastro self-service atual, ainda baseado em pacote de crédito único) **também recebe o plano Legado por padrão** (`app/services/default_subscription.py`, mesma transação do provisionamento dos 4 agentes padrão) — comportamento provisório até a Etapa 2.

### Pendências / próximas etapas
- [ ] **Etapa 2**: Stripe Subscriptions de verdade — cadastro público passa a vender os 3 planos (não mais pacote de crédito único), webhook de concessão mensal de créditos (`invoice.payment_succeeded`), endpoint de upgrade de plano (com proration, só upgrade nesta v1).
- [ ] **Etapa 3**: frontend (`apps/web`) — seleção de plano no cadastro, tela de upgrade.
- [ ] Downgrade de plano, cancelamento self-service, catálogo de ferramentas extras — fora de escopo de todas as etapas por ora.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: atualiza CLAUDE.md pra Etapa 1 de planos de assinatura"
```
