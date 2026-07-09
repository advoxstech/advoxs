# Dashboard do Escritório (`/inicio`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Página inicial pós-login do escritório (`/inicio`) com visão geral da conta — saldo, WhatsApp, conversas, consumo (30 dias) e base de conhecimento — alimentada por um endpoint agregado tenant-scoped.

**Architecture:** O `api` ganha `GET /api/v1/dashboard` (service `build_tenant_dashboard`, mesmo desenho do `build_dashboard` do admin mas escopado por tenant via `get_tenant_session` + filtro explícito). O `web` ganha a página `/inicio` (stat tiles reaproveitando o `StatTile` já existente + lista de conversas recentes) e o item "Início" no `TenantNav`; por último, os redirects pós-login passam a apontar pra `/inicio`.

**Tech Stack:** FastAPI + SQLAlchemy async (api), Next.js 15 App Router + React (web).

## Global Constraints

- **Toda query do dashboard filtrada por `tenant_id`** (defesa em profundidade: `get_tenant_session` + filtro explícito, mesmo padrão de `conversations.py`/`billing.py`).
- **Sem gráficos, sem polling** — stat tiles + uma lista, carregados uma vez no mount.
- **Rota nomeada `/inicio`** — o CLAUDE.md deixa de mencionar `/rom`.
- **Os redirects pós-login mudam por último** (Task 3) — evita apontar pra uma rota que ainda não existe durante a branch.
- Número do WhatsApp mascarado no mesmo formato de `GET /whatsapp/connection` (`{value[:3]} **** {value[-4:]}` quando `len > 7`).
- Mensagens/comentários em pt-BR com acentuação correta.
- Comandos: `apps/api` → `uv run pytest tests/unit`, `uv run ruff check .`, `uv run ruff format --check .`. `apps/web` → `pnpm test`, `pnpm lint`, `pnpm build` (via `npx --yes pnpm@9 <comando>` se `pnpm` não estiver global).

---

### Task 1: `api` — endpoint agregado `GET /dashboard`

**Files:**
- Create: `apps/api/app/schemas/dashboard.py`
- Create: `apps/api/app/services/dashboard.py`
- Create: `apps/api/app/api/v1/dashboard.py`
- Modify: `apps/api/app/api/v1/router.py`
- Test: `apps/api/tests/unit/test_tenant_dashboard.py`
- Test: `apps/api/tests/unit/test_tenant_dashboard_routes.py`

**Interfaces:**
- Consumes: `TenantContext`/`get_current_tenant`/`get_tenant_session` (`app/api/deps.py`); models `Tenant`, `WhatsAppNumber`, `Conversation`, `Message`, `CreditTransaction`, `KnowledgeBaseFile` (`app/models`).
- Produces: `build_tenant_dashboard(session, tenant_id: uuid.UUID) -> TenantDashboardOut` em `app.services.dashboard`; `GET /api/v1/dashboard` → `TenantDashboardOut` (JSON snake_case, consumido pela Task 2 como tipo TS `TenantDashboard`).

- [ ] **Step 1: Schemas**

Criar `apps/api/app/schemas/dashboard.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WhatsappStatusOut(BaseModel):
    connected: bool
    display_phone_number: str | None


class ConversationsSummaryOut(BaseModel):
    total: int
    waiting_human: int


class UsageSummaryOut(BaseModel):
    agent_messages: int
    credits_consumed: int


class KnowledgeBaseSummaryOut(BaseModel):
    ready: int
    error: int


class RecentConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_phone_number: str
    state: str
    last_message_at: datetime | None


class TenantDashboardOut(BaseModel):
    credit_balance: int
    whatsapp: WhatsappStatusOut
    conversations: ConversationsSummaryOut
    usage_last_30_days: UsageSummaryOut
    knowledge_base: KnowledgeBaseSummaryOut
    recent_conversations: list[RecentConversationOut]
```

- [ ] **Step 2: Escrever o teste do service que falha**

Criar `apps/api/tests/unit/test_tenant_dashboard.py`. **Ordem das chamadas travada** (o teste mocka por posição — se a ordem no service mudar, os `side_effect` mudam junto): 8 chamadas a `session.scalar` na ordem [credit_balance, display_phone_number, conversations_total, waiting_human, agent_messages, credits_consumed_negative, kb_ready, kb_error] e 1 chamada a `session.execute` (recent conversations).

```python
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
        assert len(result.recent_conversations) == 2

    async def test_sem_whatsapp_conectado_retorna_disconnected(self, session) -> None:
        session.scalar = AsyncMock(side_effect=[0, None, 0, 0, 0, 0, 0, 0])
        session.execute = AsyncMock(return_value=_execute_result([]))

        result = await build_tenant_dashboard(session, TENANT_ID)

        assert result.whatsapp.connected is False
        assert result.whatsapp.display_phone_number is None
        assert result.recent_conversations == []

    async def test_todas_as_queries_filtram_por_tenant(self, session) -> None:
        """Isolamento: nenhuma query do dashboard pode esquecer o filtro de
        tenant — mesma classe de bug do vazamento corrigido em billing/status."""
        session.scalar = AsyncMock(side_effect=[0, None, 0, 0, 0, 0, 0, 0])
        session.execute = AsyncMock(return_value=_execute_result([]))

        await build_tenant_dashboard(session, TENANT_ID)

        # A 1ª query filtra por tenants.id (a PK do próprio tenant); todas as
        # demais filtram pela coluna tenant_id das tabelas tenant-scoped.
        scalar_sqls = [str(call.args[0]) for call in session.scalar.await_args_list]
        assert "tenants.id" in scalar_sqls[0]
        for sql in scalar_sqls[1:]:
            assert "tenant_id" in sql
        execute_sql = str(session.execute.await_args_list[0].args[0])
        assert "tenant_id" in execute_sql
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_tenant_dashboard.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'app.services.dashboard'`.

- [ ] **Step 4: Service**

Criar `apps/api/app/services/dashboard.py`:

```python
"""Snapshot agregado do painel do escritório — todas as queries filtradas
pelo tenant autenticado (defesa em profundidade junto com o RLS da sessão)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Conversation,
    CreditTransaction,
    KnowledgeBaseFile,
    Message,
    Tenant,
    WhatsAppNumber,
)
from app.schemas.dashboard import (
    ConversationsSummaryOut,
    KnowledgeBaseSummaryOut,
    RecentConversationOut,
    TenantDashboardOut,
    UsageSummaryOut,
    WhatsappStatusOut,
)

PERIOD_DAYS = 30
RECENT_LIMIT = 5


def _mask_phone_number(value: str) -> str:
    """Mesmo formato de GET /whatsapp/connection: DDI + 4 últimos dígitos."""
    if len(value) <= 7:
        return value
    return f"{value[:3]} **** {value[-4:]}"


async def build_tenant_dashboard(
    session: AsyncSession, tenant_id: uuid.UUID
) -> TenantDashboardOut:
    since = datetime.now(UTC) - timedelta(days=PERIOD_DAYS)

    credit_balance = (
        await session.scalar(select(Tenant.credit_balance).where(Tenant.id == tenant_id))
    ) or 0

    display_phone_number = await session.scalar(
        select(WhatsAppNumber.display_phone_number).where(
            WhatsAppNumber.tenant_id == tenant_id, WhatsAppNumber.status == "connected"
        )
    )

    conversations_total = (
        await session.scalar(
            select(func.count(Conversation.id)).where(Conversation.tenant_id == tenant_id)
        )
    ) or 0
    waiting_human = (
        await session.scalar(
            select(func.count(Conversation.id)).where(
                Conversation.tenant_id == tenant_id, Conversation.state == "human"
            )
        )
    ) or 0

    agent_messages = (
        await session.scalar(
            select(func.count(Message.id)).where(
                Message.tenant_id == tenant_id,
                Message.sender_type == "agent",
                Message.created_at >= since,
            )
        )
    ) or 0
    credits_consumed_negative = (
        await session.scalar(
            select(func.coalesce(func.sum(CreditTransaction.amount_credits), 0)).where(
                CreditTransaction.tenant_id == tenant_id,
                CreditTransaction.type == "consumption",
                CreditTransaction.created_at >= since,
            )
        )
    ) or 0

    kb_ready = (
        await session.scalar(
            select(func.count(KnowledgeBaseFile.id)).where(
                KnowledgeBaseFile.tenant_id == tenant_id, KnowledgeBaseFile.status == "ready"
            )
        )
    ) or 0
    kb_error = (
        await session.scalar(
            select(func.count(KnowledgeBaseFile.id)).where(
                KnowledgeBaseFile.tenant_id == tenant_id, KnowledgeBaseFile.status == "error"
            )
        )
    ) or 0

    recent = (
        (
            await session.execute(
                select(Conversation)
                .where(Conversation.tenant_id == tenant_id)
                .order_by(Conversation.last_message_at.desc().nulls_last())
                .limit(RECENT_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    return TenantDashboardOut(
        credit_balance=credit_balance,
        whatsapp=WhatsappStatusOut(
            connected=display_phone_number is not None,
            display_phone_number=(
                _mask_phone_number(display_phone_number) if display_phone_number else None
            ),
        ),
        conversations=ConversationsSummaryOut(
            total=conversations_total, waiting_human=waiting_human
        ),
        usage_last_30_days=UsageSummaryOut(
            agent_messages=agent_messages, credits_consumed=abs(credits_consumed_negative)
        ),
        knowledge_base=KnowledgeBaseSummaryOut(ready=kb_ready, error=kb_error),
        recent_conversations=[RecentConversationOut.model_validate(c) for c in recent],
    )
```

- [ ] **Step 5: Rodar o teste do service e ver passar**

Run: `cd apps/api && uv run pytest tests/unit/test_tenant_dashboard.py -v`
Expected: PASS (3/3).

- [ ] **Step 6: Escrever o teste da rota que falha**

Criar `apps/api/tests/unit/test_tenant_dashboard_routes.py`:

```python
import uuid
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import app.api.v1.dashboard as dashboard_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app
from app.schemas.dashboard import (
    ConversationsSummaryOut,
    KnowledgeBaseSummaryOut,
    TenantDashboardOut,
    UsageSummaryOut,
    WhatsappStatusOut,
)

TENANT_ID = uuid.uuid4()


def _dummy_dashboard() -> TenantDashboardOut:
    return TenantDashboardOut(
        credit_balance=1000,
        whatsapp=WhatsappStatusOut(connected=True, display_phone_number="551 **** 4321"),
        conversations=ConversationsSummaryOut(total=2, waiting_human=1),
        usage_last_30_days=UsageSummaryOut(agent_messages=10, credits_consumed=5),
        knowledge_base=KnowledgeBaseSummaryOut(ready=3, error=0),
        recent_conversations=[],
    )


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/dashboard")
    assert response.status_code == 401


def test_com_token_retorna_o_dashboard(monkeypatch) -> None:
    async def override_tenant():
        return TenantContext(user_id=uuid.uuid4(), tenant_id=TENANT_ID, role="admin")

    async def override_session():
        yield AsyncMock()

    build = AsyncMock(return_value=_dummy_dashboard())
    monkeypatch.setattr(dashboard_module, "build_tenant_dashboard", build)
    app.dependency_overrides[get_current_tenant] = override_tenant
    app.dependency_overrides[get_tenant_session] = override_session
    try:
        response = TestClient(app).get("/api/v1/dashboard")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["credit_balance"] == 1000
    assert body["whatsapp"]["connected"] is True
    # O tenant_id passado ao service vem do contexto autenticado.
    assert build.await_args.args[1] == TENANT_ID
```

- [ ] **Step 7: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_tenant_dashboard_routes.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'app.api.v1.dashboard'`.

- [ ] **Step 8: Rota + registro no router**

Criar `apps/api/app/api/v1/dashboard.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.schemas.dashboard import TenantDashboardOut
from app.services.dashboard import build_tenant_dashboard

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
async def get_dashboard(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> TenantDashboardOut:
    return await build_tenant_dashboard(session, ctx.tenant_id)
```

Em `apps/api/app/api/v1/router.py`, adicionar (ordem alfabética dos imports/includes):

```python
from app.api.v1.dashboard import router as dashboard_router
```

```python
api_router.include_router(dashboard_router)
```

- [ ] **Step 9: Rodar a suíte completa e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos PASS, ruff limpo.

- [ ] **Step 10: Commit**

```bash
git add apps/api/app/schemas/dashboard.py apps/api/app/services/dashboard.py apps/api/app/api/v1/dashboard.py apps/api/app/api/v1/router.py apps/api/tests/unit/test_tenant_dashboard.py apps/api/tests/unit/test_tenant_dashboard_routes.py
git commit -m "feat(api): endpoint agregado do dashboard do escritório (GET /dashboard)"
```

---

### Task 2: `web` — página `/inicio`, `DashboardPanel` e item "Início" na nav

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/backend.ts`
- Modify: `apps/web/src/components/TenantNav.tsx`
- Modify: `apps/web/__tests__/TenantNav.test.tsx`
- Create: `apps/web/src/components/DashboardPanel.tsx`
- Create: `apps/web/src/app/inicio/page.tsx`
- Test: `apps/web/__tests__/DashboardPanel.test.tsx`

**Interfaces:**
- Consumes: `backendFetch` (`@/lib/client-api`); `StatTile` (`@/components/StatTile`, já existente — `{label, value, tone?: "neutral"|"good"|"warning"|"critical"}`); `TenantNav`/`LowBalanceBanner` (já existentes); `GET dashboard` (Task 1, forma de `TenantDashboardOut`).
- Produces: tipo `TenantDashboard` em `@/lib/types`; `DashboardPanel()` em `@/components/DashboardPanel`; `TenantNavItem` ganha `"inicio"`.

- [ ] **Step 1: Tipo `TenantDashboard`**

Em `apps/web/src/lib/types.ts`, adicionar ao final:

```ts
export interface TenantDashboard {
  credit_balance: number;
  whatsapp: { connected: boolean; display_phone_number: string | null };
  conversations: { total: number; waiting_human: number };
  usage_last_30_days: { agent_messages: number; credits_consumed: number };
  knowledge_base: { ready: number; error: number };
  recent_conversations: {
    id: string;
    contact_phone_number: string;
    state: "agent" | "human";
    last_message_at: string | null;
  }[];
}
```

- [ ] **Step 2: Allowlist do proxy**

Em `apps/web/src/lib/backend.ts`, trocar:

```ts
const ALLOWED_PREFIXES = ["conversations", "knowledge-base", "whatsapp", "signup", "billing"];
```

por:

```ts
const ALLOWED_PREFIXES = [
  "conversations",
  "knowledge-base",
  "whatsapp",
  "signup",
  "billing",
  "dashboard",
];
```

- [ ] **Step 3: Item "Início" no `TenantNav`**

Em `apps/web/src/components/TenantNav.tsx`, trocar o tipo e a lista:

```tsx
type TenantNavItem = "inicio" | "conversas" | "base" | "config" | "creditos";

const ITEMS: { key: TenantNavItem; href: string; label: string }[] = [
  { key: "inicio", href: "/inicio", label: "Início" },
  { key: "conversas", href: "/conversas", label: "Conversas" },
  { key: "base", href: "/base-de-conhecimento", label: "Base" },
  { key: "config", href: "/configuracoes/whatsapp", label: "Config" },
  { key: "creditos", href: "/creditos", label: "Créditos" },
];
```

(nenhuma outra mudança no componente.)

Em `apps/web/__tests__/TenantNav.test.tsx`, adicionar ao primeiro teste (`active="conversas"`):

```tsx
    expect(screen.getByText("Início").closest("a")).toHaveAttribute("href", "/inicio");
```

E adicionar um teste novo:

```tsx
  it("marca inicio como ativo quando active='inicio'", () => {
    render(<TenantNav active="inicio" />);

    expect(screen.getByText("Início").closest("a")).toBeNull();
    expect(screen.getByText("Conversas").closest("a")).toHaveAttribute("href", "/conversas");
  });
```

- [ ] **Step 4: Escrever o teste do `DashboardPanel` que falha**

Criar `apps/web/__tests__/DashboardPanel.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardPanel } from "@/components/DashboardPanel";
import { backendFetch } from "@/lib/client-api";
import type { TenantDashboard } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const DASHBOARD: TenantDashboard = {
  credit_balance: 1500,
  whatsapp: { connected: true, display_phone_number: "551 **** 4321" },
  conversations: { total: 12, waiting_human: 3 },
  usage_last_30_days: { agent_messages: 87, credits_consumed: 240 },
  knowledge_base: { ready: 5, error: 1 },
  recent_conversations: [
    {
      id: "c1",
      contact_phone_number: "5511999990001",
      state: "agent",
      last_message_at: "2026-07-08T12:00:00Z",
    },
    {
      id: "c2",
      contact_phone_number: "5511999990002",
      state: "human",
      last_message_at: null,
    },
  ],
};

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("DashboardPanel", () => {
  it("renderiza as métricas do dashboard", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => DASHBOARD });

    render(<DashboardPanel />);

    await waitFor(() => expect(screen.getByText("1500")).toBeInTheDocument());
    expect(screen.getByText("551 **** 4321")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("87")).toBeInTheDocument();
    expect(screen.getByText("240")).toBeInTheDocument();
  });

  it("renderiza as conversas recentes com estado traduzido", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => DASHBOARD });

    render(<DashboardPanel />);

    await waitFor(() => expect(screen.getByText("5511999990001")).toBeInTheDocument());
    expect(screen.getByText("agente")).toBeInTheDocument();
    expect(screen.getByText("humano")).toBeInTheDocument();
  });

  it("mostra 'Desconectado' quando não há WhatsApp conectado", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        ...DASHBOARD,
        whatsapp: { connected: false, display_phone_number: null },
      }),
    });

    render(<DashboardPanel />);

    await waitFor(() => expect(screen.getByText("Desconectado")).toBeInTheDocument());
  });

  it("mostra mensagem neutra quando não há conversas", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ ...DASHBOARD, recent_conversations: [] }),
    });

    render(<DashboardPanel />);

    await waitFor(() => expect(screen.getByText("Nenhuma conversa ainda.")).toBeInTheDocument());
  });

  it("mostra erro quando o dashboard falha ao carregar", async () => {
    mockedFetch.mockResolvedValue({ ok: false, status: 500 });

    render(<DashboardPanel />);

    await waitFor(() =>
      expect(screen.getByText("Não foi possível carregar o painel.")).toBeInTheDocument(),
    );
  });
});
```

- [ ] **Step 5: Rodar e ver falhar**

Run: `cd apps/web && npx --yes pnpm@9 test -- DashboardPanel`
Expected: FAIL — `@/components/DashboardPanel` não existe.

- [ ] **Step 6: Criar `DashboardPanel`**

Criar `apps/web/src/components/DashboardPanel.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type { TenantDashboard } from "@/lib/types";

import { StatTile } from "./StatTile";

const STATE_LABEL: Record<"agent" | "human", string> = {
  agent: "agente",
  human: "humano",
};

export function DashboardPanel() {
  const [data, setData] = useState<TenantDashboard | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const response = await backendFetch("dashboard");
        if (response.ok) {
          setData(await response.json());
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, []);

  if (!loaded) {
    return <p className="p-8 text-sm text-muted">Carregando...</p>;
  }
  if (!data) {
    return <p className="p-8 text-sm text-danger">Não foi possível carregar o painel.</p>;
  }

  return (
    <div className="flex flex-col gap-8 p-8">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
        <Link href="/creditos">
          <StatTile
            label="Saldo de créditos"
            value={String(data.credit_balance)}
            tone={data.credit_balance <= 0 ? "critical" : "neutral"}
          />
        </Link>
        <Link href="/configuracoes/whatsapp">
          <StatTile
            label="WhatsApp"
            value={
              data.whatsapp.connected
                ? (data.whatsapp.display_phone_number ?? "Conectado")
                : "Desconectado"
            }
            tone={data.whatsapp.connected ? "good" : "critical"}
          />
        </Link>
        <Link href="/conversas">
          <StatTile label="Conversas" value={String(data.conversations.total)} />
        </Link>
        <Link href="/conversas">
          <StatTile
            label="Aguardando você"
            value={String(data.conversations.waiting_human)}
            tone={data.conversations.waiting_human > 0 ? "warning" : "neutral"}
          />
        </Link>
        <StatTile
          label="Respostas do agente (30 dias)"
          value={String(data.usage_last_30_days.agent_messages)}
        />
        <StatTile
          label="Créditos consumidos (30 dias)"
          value={String(data.usage_last_30_days.credits_consumed)}
        />
        <Link href="/base-de-conhecimento">
          <StatTile label="Arquivos na base" value={String(data.knowledge_base.ready)} />
        </Link>
        {data.knowledge_base.error > 0 && (
          <Link href="/base-de-conhecimento">
            <StatTile
              label="Arquivos com erro"
              value={String(data.knowledge_base.error)}
              tone="critical"
            />
          </Link>
        )}
      </div>

      <div>
        <h2 className="font-display text-lg font-semibold text-ink">Conversas recentes</h2>
        <ul className="mt-3 divide-y divide-line rounded-sm border border-line bg-surface">
          {data.recent_conversations.map((c) => (
            <li key={c.id}>
              <Link
                href="/conversas"
                className="flex items-center justify-between px-4 py-3 text-sm hover:bg-ground"
              >
                <span className="text-ink">{c.contact_phone_number}</span>
                <span className="flex items-center gap-4">
                  <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted">
                    {STATE_LABEL[c.state]}
                  </span>
                  <span className="text-muted">
                    {c.last_message_at
                      ? new Date(c.last_message_at).toLocaleString("pt-BR")
                      : "—"}
                  </span>
                </span>
              </Link>
            </li>
          ))}
          {data.recent_conversations.length === 0 && (
            <li className="px-4 py-3 text-sm text-muted">Nenhuma conversa ainda.</li>
          )}
        </ul>
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Página `/inicio`**

Criar `apps/web/src/app/inicio/page.tsx`:

```tsx
import { DashboardPanel } from "@/components/DashboardPanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default function InicioPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="inicio" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <main className="flex-1 overflow-y-auto bg-ground">
          <DashboardPanel />
        </main>
      </div>
    </div>
  );
}
```

- [ ] **Step 8: Rodar toda a suíte, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde; build lista `/inicio`.

- [ ] **Step 9: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/lib/backend.ts apps/web/src/components/TenantNav.tsx apps/web/__tests__/TenantNav.test.tsx apps/web/src/components/DashboardPanel.tsx apps/web/src/app/inicio/page.tsx apps/web/__tests__/DashboardPanel.test.tsx
git commit -m "feat(web): dashboard do escritório em /inicio"
```

---

### Task 3: Redirects pós-login, `CLAUDE.md` e verificação local

**Files:**
- Modify: `apps/web/src/app/login/actions.ts`
- Modify: `apps/web/src/middleware.ts`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: página `/inicio` (Task 2, precisa existir antes dos redirects apontarem pra ela).

- [ ] **Step 1: Redirect da server action de login**

Em `apps/web/src/app/login/actions.ts`, trocar:

```ts
  redirect("/conversas");
```

por:

```ts
  redirect("/inicio");
```

- [ ] **Step 2: Redirects do middleware + matcher**

Em `apps/web/src/middleware.ts`:

1. No branch de `pathname === "/"` com sessão, trocar `new URL("/conversas", request.url)` por `new URL("/inicio", request.url)`.
2. No branch de `pathname === "/login"` com sessão, trocar `new URL("/conversas", request.url)` por `new URL("/inicio", request.url)`.
3. No `config.matcher`, adicionar `"/inicio/:path*"` (junto das outras entradas do painel do tenant, antes de `"/admin/:path*"`).

O bloco de `/admin` e a lógica de tenant sem sessão (`pathname !== "/login" && !hasSession → /login`) não mudam.

- [ ] **Step 3: Rodar teste, lint e build do web**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde.

- [ ] **Step 4: Atualizar o CLAUDE.md**

- Seção "Frontend" (item `- **`/rom`**` na lista de páginas): reescrever como `- **`/inicio`** — ✅ implementada: página inicial pós-login (dashboard do escritório): saldo de créditos, status do WhatsApp, conversas (total + aguardando humano), consumo dos últimos 30 dias (respostas do agente + créditos), base de conhecimento (prontos/erros) e as 5 conversas mais recentes. Alimentada por `GET /api/v1/dashboard` (endpoint agregado tenant-scoped, mesmo desenho do dashboard do admin). O pós-login (login + redirects do middleware) aponta pra cá.`
- Seção "Estado atual do repositório", linha do `api`: acrescentar o **dashboard do escritório** — `/api/v1/dashboard` — à lista de implementados e remover "Ainda **não** tem: dashboard `/rom`".
- Seção "Estado atual do repositório", linha do `web`: acrescentar `/inicio` à lista de implementados e remover "Ainda não tem: `/rom` (dashboard do escritório — não confundir com `/admin`)".

- [ ] **Step 5: Build e verificação local**

```bash
docker compose up -d --build api web
```

1. Login com o tenant de seed (`admin@demo.com`/`segredo123`), pegar o `access_token`.
2. `curl http://localhost:8000/api/v1/dashboard -H "Authorization: Bearer <token>"` — deve retornar o JSON com todas as chaves (`credit_balance`, `whatsapp`, `conversations`, `usage_last_30_days`, `knowledge_base`, `recent_conversations`).
3. Sem token → `401`.
4. Fazer login pelo browser em `http://localhost:3001/login` — deve cair em `/inicio` (não mais `/conversas`) e o dashboard renderizar com os dados reais do tenant.
5. Acessar `http://localhost:3001/` logado — redirect pra `/inicio`.
6. Acessar `/inicio` sem sessão — redirect pra `/login`.
7. Conferir os links dos tiles (saldo → `/creditos`, WhatsApp → `/configuracoes/whatsapp`, base → `/base-de-conhecimento`, conversas → `/conversas`).

Expected: todos os passos funcionam; o passo 4 (pós-login cai em `/inicio`) é a mudança de comportamento central.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/app/login/actions.ts apps/web/src/middleware.ts CLAUDE.md
git commit -m "feat(web): /inicio vira a página inicial pós-login"
```
