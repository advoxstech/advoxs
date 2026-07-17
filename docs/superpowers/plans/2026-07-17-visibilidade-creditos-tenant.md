# Visibilidade de Créditos para o Tenant — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar ao tenant 3 telas de visibilidade sobre o próprio gasto de créditos — extrato geral em `/creditos`, consumo por conversa numa nova aba em `/conversas`, e saldo/consumo dos clientes finais em `/configuracoes/cobranca-clientes` — sem nunca mencionar "tokens" na UI.

**Architecture:** Três endpoints novos, tenant-scoped (RLS via `get_tenant_session`), reaproveitando os padrões já existentes no `api` (schemas Pydantic, agregação com `select(...).group_by(...)` como em `app/services/admin_dashboard.py`). No `web`, três componentes novos, cada um responsável por buscar e renderizar sua própria lista — sem tocar `TenantNav`.

**Tech Stack:** FastAPI + SQLAlchemy 2 async (api), Next.js + Vitest (web). Ver spec completa em `docs/superpowers/specs/2026-07-17-visibilidade-creditos-tenant-design.md`.

## Global Constraints

- **Nunca a palavra "tokens" em texto voltado ao tenant** — nem em componentes React, nem em `description` gravada no ledger. Regra da spec, sem exceção.
- Extrato: sem filtro na v1, lista paginável via `limit`/`offset` (backend), frontend busca uma página só (até 50 itens, sem "carregar mais").
- Consumo por conversa: `from`/`to` obrigatórios (datas), fonte é `messages.credits_consumed` (não o ledger) — resumos sob demanda não aparecem aqui (limitação documentada na spec, não resolver agora).
- Clientes finais: só renderiza quando `tenant_billing_settings.enabled === true`.
- Nenhuma mudança em `TenantNav.tsx` (histórico de regressões).
- Commits Conventional em pt-BR; `uv run ruff check .` + `uv run pytest tests/unit` em `apps/api`; `pnpm test && pnpm lint` em `apps/web`.
- ⚠️ Arquivos com formatação pré-existente não commitada (`.claude/settings.local.json`) ficam fora dos commits — `git add` sempre com paths explícitos.

---

### Task 1: Remover "tokens" das descrições gravadas no ledger

**Files:**
- Modify: `apps/worker/app/tasks/messages.py:414`, `apps/worker/app/tasks/messages.py:458`
- Modify: `apps/api/app/api/v1/conversations.py:236`
- Modify: `apps/api/app/services/test_conversations.py:92`
- Test: `apps/worker/tests/unit/test_debitar_creditos_cliente_final.py`, `apps/api/tests/unit/test_conversations_routes.py`, `apps/api/tests/unit/test_test_conversations_routes.py`

**Interfaces:**
- Não altera nenhuma assinatura de função — só o texto literal do campo `description` gravado em `credit_transactions`/`end_customer_credit_transactions`. Tasks 2-7 dependem de `description` nunca conter "token" (é isso que o extrato da Task 3 exibe).

- [ ] **Step 1: Adicionar as asserções que devem falhar** — em `apps/worker/tests/unit/test_debitar_creditos_cliente_final.py`, no final de `test_debito_do_tenant_grava_tokens_brutos_e_config`, adicionar:

```python
    assert "token" not in transaction["description"].lower()
```

E no final de `test_lanca_consumption_negativo_e_atualiza_saldo`, adicionar a mesma linha:

```python
    assert "token" not in transaction["description"].lower()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/worker && UV_PROJECT_ENVIRONMENT=/tmp/venv-worker uv run pytest tests/unit/test_debitar_creditos_cliente_final.py -v`
Expected: FAIL — `assert "token" not in "consumo do agente (2000 tokens)"`

- [ ] **Step 3: Corrigir as descrições no worker** — em `apps/worker/app/tasks/messages.py`, trocar as duas ocorrências (linhas 414 e 458) de:

```python
            description=f"Consumo do agente ({tokens_used} tokens)",
```
por:
```python
            description="Consumo do agente",
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/worker && UV_PROJECT_ENVIRONMENT=/tmp/venv-worker uv run pytest tests/unit -v`
Expected: PASS (todos)

- [ ] **Step 5: Mesma correção no `api`** — adicionar a asserção que falha primeiro. Em `apps/api/tests/unit/test_conversations_routes.py`, no final de `test_gera_resumo_persiste_e_debita_creditos` (depois de `assert added.pricing_config_id == pricing.id`), adicionar:

```python
        assert "token" not in added.description.lower()
```

Em `apps/api/tests/unit/test_test_conversations_routes.py`, no final de `test_fluxo_feliz_persiste_e_debita` (depois de `assert transaction.tokens_output == 700`), adicionar:

```python
        assert "token" not in transaction.description.lower()
```

- [ ] **Step 6: Rodar e ver falhar**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit/test_conversations_routes.py tests/unit/test_test_conversations_routes.py -v`
Expected: 2 FAIL

- [ ] **Step 7: Corrigir as descrições no api** — em `apps/api/app/api/v1/conversations.py:236`, trocar:

```python
                description=f"Resumo de conversa gerado ({tokens_used} tokens)",
```
por:
```python
                description="Resumo de conversa gerado",
```

Em `apps/api/app/services/test_conversations.py:92`, trocar:
```python
                description=f"Consumo do agente em conversa de teste ({tokens_used} tokens)",
```
por:
```python
                description="Consumo do agente em conversa de teste",
```

- [ ] **Step 8: Rodar toda a suíte do api + lint**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit -v && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run ruff check .`
Expected: PASS, All checks passed!

- [ ] **Step 9: Rodar a suíte do worker + lint (confirmação final)**

Run: `cd apps/worker && UV_PROJECT_ENVIRONMENT=/tmp/venv-worker uv run pytest tests/unit -v && UV_PROJECT_ENVIRONMENT=/tmp/venv-worker uv run ruff check .`
Expected: PASS, All checks passed!

- [ ] **Step 10: Commit**

```bash
git add apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_debitar_creditos_cliente_final.py \
  apps/api/app/api/v1/conversations.py apps/api/app/services/test_conversations.py \
  apps/api/tests/unit/test_conversations_routes.py apps/api/tests/unit/test_test_conversations_routes.py
git commit -m "fix: remove menção a tokens das descrições gravadas no ledger de créditos"
```

---

### Task 2: `api` — extrato geral (`GET /billing/transactions`)

**Files:**
- Modify: `apps/api/app/schemas/billing.py`
- Modify: `apps/api/app/api/v1/billing.py`
- Test: `apps/api/tests/unit/test_billing_routes.py`

**Interfaces:**
- Produces: schema `BillingTransactionOut {id: UUID, type: str, amount_credits: float, description: str|None, created_at: datetime}`; rota `GET /api/v1/billing/transactions?limit=&offset=` → `list[BillingTransactionOut]`, ordenada por `created_at desc`. Consumida pela Task 3 (frontend).

- [ ] **Step 1: Escrever os testes que falham** — em `apps/api/tests/unit/test_billing_routes.py`, adicionar aos imports do topo:

```python
from datetime import UTC, datetime
from types import SimpleNamespace
```

(ficam ao lado de `import uuid` e `from unittest.mock import AsyncMock, MagicMock` já existentes). Adicionar a classe no final do arquivo:

```python
class TestTransactions:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).get("/api/v1/billing/transactions")
        assert response.status_code == 401

    def test_lista_paginada_ordenada_por_data_desc(self, client, session) -> None:
        rows = [
            SimpleNamespace(
                id=uuid.uuid4(),
                type="purchase",
                amount_credits=1000.0,
                description="Compra do pacote Starter",
                created_at=datetime(2026, 7, 10, tzinfo=UTC),
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                type="consumption",
                amount_credits=-1.75,
                description="Consumo do agente",
                created_at=datetime(2026, 7, 9, tzinfo=UTC),
            ),
        ]
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        session.execute.return_value = result

        response = client.get("/api/v1/billing/transactions")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        assert body[0]["type"] == "purchase"
        assert body[0]["amount_credits"] == 1000.0
        assert body[1]["amount_credits"] == -1.75

    def test_query_filtra_por_tenant_id(self, client, session) -> None:
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        session.execute.return_value = result

        response = client.get("/api/v1/billing/transactions")

        assert response.status_code == 200
        query = session.execute.call_args.args[0]
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
        assert "tenant_id" in compiled
        assert TENANT_ID.hex in compiled.replace("-", "")

    def test_respeita_limit_e_offset(self, client, session) -> None:
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        session.execute.return_value = result

        response = client.get("/api/v1/billing/transactions?limit=10&offset=20")

        assert response.status_code == 200
        query = session.execute.call_args.args[0]
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
        assert "LIMIT 10" in compiled
        assert "OFFSET 20" in compiled
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit/test_billing_routes.py::TestTransactions -v`
Expected: FAIL — `404 Not Found` (rota ainda não existe)

- [ ] **Step 3: Adicionar o schema** — em `apps/api/app/schemas/billing.py`, trocar o topo do arquivo:

```python
import uuid

from pydantic import BaseModel
```
por:
```python
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
```

E adicionar ao final do arquivo:

```python
class BillingTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    amount_credits: float
    description: str | None
    created_at: datetime
```

- [ ] **Step 4: Adicionar a rota** — em `apps/api/app/api/v1/billing.py`, o bloco de imports do topo precisa ficar assim:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.core.db import get_system_session
from app.models import CreditTransaction, Tenant
from app.schemas.billing import (
    BillingBalanceOut,
    BillingCheckoutRequest,
    BillingCheckoutUrlOut,
    BillingStatusOut,
    BillingTransactionOut,
)
from app.services.billing import (
    InvalidPackageError,
    StripeApiError,
    create_recompra_checkout_session,
)
```

Adicionar, depois da rota `get_balance`:

```python
@router.get("/transactions")
async def list_transactions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[BillingTransactionOut]:
    result = await session.execute(
        select(CreditTransaction)
        .where(CreditTransaction.tenant_id == ctx.tenant_id)
        .order_by(CreditTransaction.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [BillingTransactionOut.model_validate(t) for t in result.scalars().all()]
```

Adicionar `Query` ao import do FastAPI no topo do arquivo — trocar `from fastapi import APIRouter, Depends, HTTPException, status` por `from fastapi import APIRouter, Depends, HTTPException, Query, status`.

- [ ] **Step 5: Rodar e ver passar**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit/test_billing_routes.py -v`
Expected: PASS (todos)

- [ ] **Step 6: Rodar suíte inteira + lint**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run ruff check .`
Expected: PASS, All checks passed!

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/schemas/billing.py apps/api/app/api/v1/billing.py apps/api/tests/unit/test_billing_routes.py
git commit -m "feat(api): extrato geral de transações do tenant (GET /billing/transactions)"
```

---

### Task 3: `web` — extrato geral em `/creditos`

**Files:**
- Create: `apps/web/src/components/CreditosExtrato.tsx`
- Modify: `apps/web/src/app/creditos/page.tsx`
- Test: `apps/web/__tests__/CreditosExtrato.test.tsx`

**Interfaces:**
- Consumes: `GET /api/v1/billing/transactions` (Task 2), `formatCredits`/`formatFullDateTime` de `@/lib/format`.
- Produces: componente `CreditosExtrato` (sem props), renderizado em `creditos/page.tsx`.

- [ ] **Step 1: Escrever o teste** — criar `apps/web/__tests__/CreditosExtrato.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CreditosExtrato } from "@/components/CreditosExtrato";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("CreditosExtrato", () => {
  it("mostra estado vazio quando não há transações", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => [] });

    render(<CreditosExtrato />);

    await waitFor(() => expect(screen.getByText("Nenhuma transação ainda.")).toBeInTheDocument());
  });

  it("lista as transações com tipo traduzido e créditos formatados", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          id: "t1",
          type: "purchase",
          amount_credits: 1000,
          description: "Compra do pacote Starter",
          created_at: "2026-07-10T12:00:00Z",
        },
        {
          id: "t2",
          type: "consumption",
          amount_credits: -1.75,
          description: null,
          created_at: "2026-07-09T12:00:00Z",
        },
      ],
    });

    render(<CreditosExtrato />);

    await waitFor(() => expect(screen.getByText("Compra do pacote Starter")).toBeInTheDocument());
    expect(screen.getByText("+1.000")).toBeInTheDocument();
    expect(screen.getByText("Consumo")).toBeInTheDocument();
    expect(screen.getByText("-1,75")).toBeInTheDocument();
  });

  it("nunca menciona tokens na tela", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          id: "t1",
          type: "consumption",
          amount_credits: -2,
          description: "Consumo do agente",
          created_at: "2026-07-09T12:00:00Z",
        },
      ],
    });

    const { container } = render(<CreditosExtrato />);

    await waitFor(() => expect(screen.getByText("Consumo do agente")).toBeInTheDocument());
    expect(container.textContent?.toLowerCase()).not.toContain("token");
  });
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/web && pnpm vitest run __tests__/CreditosExtrato.test.tsx`
Expected: FAIL — `Cannot find module '@/components/CreditosExtrato'`

- [ ] **Step 3: Implementar o componente** — criar `apps/web/src/components/CreditosExtrato.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import { formatCredits, formatFullDateTime } from "@/lib/format";

type Transaction = {
  id: string;
  type: string;
  amount_credits: number;
  description: string | null;
  created_at: string;
};

const TYPE_LABEL: Record<string, string> = {
  purchase: "Compra",
  consumption: "Consumo",
  resale: "Revenda",
  adjustment: "Ajuste",
  refund: "Reembolso",
  bonus: "Bônus",
};

export function CreditosExtrato() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const response = await backendFetch("billing/transactions");
        if (response.ok) {
          setTransactions(await response.json());
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, []);

  return (
    <div>
      <h2 className="font-display text-lg font-semibold text-ink">Extrato</h2>
      {!loaded ? (
        <p className="mt-3 text-sm text-muted">Carregando...</p>
      ) : (
        <ul className="mt-3 divide-y divide-line rounded-sm border border-line bg-surface">
          {transactions.length === 0 && (
            <li className="px-4 py-3 text-sm text-muted">Nenhuma transação ainda.</li>
          )}
          {transactions.map((t) => (
            <li key={t.id} className="flex items-center justify-between px-4 py-3 text-sm">
              <div>
                <p className="text-ink">{t.description ?? TYPE_LABEL[t.type] ?? t.type}</p>
                <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted">
                  {TYPE_LABEL[t.type] ?? t.type} · {formatFullDateTime(t.created_at)}
                </p>
              </div>
              <span
                className={`font-mono text-sm ${t.amount_credits < 0 ? "text-danger" : "text-accent"}`}
              >
                {t.amount_credits > 0 ? "+" : ""}
                {formatCredits(t.amount_credits)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/web && pnpm vitest run __tests__/CreditosExtrato.test.tsx`
Expected: PASS (3 testes)

- [ ] **Step 5: Ligar na página** — em `apps/web/src/app/creditos/page.tsx`, trocar o import e o corpo:

```tsx
import { CreditosExtrato } from "@/components/CreditosExtrato";
import { CreditosPanel } from "@/components/CreditosPanel";
import { TenantNav } from "@/components/TenantNav";
import { API_URL } from "@/lib/backend";
import type { CreditPackage } from "@/lib/types";
```

e o `<main>`:

```tsx
      <main className="flex-1 overflow-y-auto bg-ground">
        <CreditosPanel packages={packages} />
        <div className="px-8 pb-8">
          <CreditosExtrato />
        </div>
      </main>
```

- [ ] **Step 6: Rodar a suíte inteira do web + lint**

Run: `cd apps/web && pnpm test && pnpm lint`
Expected: PASS, sem erros novos

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/components/CreditosExtrato.tsx apps/web/src/app/creditos/page.tsx apps/web/__tests__/CreditosExtrato.test.tsx
git commit -m "feat(web): extrato de créditos em /creditos"
```

---

### Task 4: `api` — consumo por conversa (`GET /conversations/usage`)

**Files:**
- Modify: `apps/api/app/schemas/conversations.py`
- Create: `apps/api/app/services/conversations_usage.py`
- Modify: `apps/api/app/api/v1/conversations.py`
- Test: `apps/api/tests/unit/test_conversations_usage_routes.py` (novo), `apps/api/tests/unit/test_conversations_usage_service.py` (novo)

**Interfaces:**
- Produces: schema `ConversationUsageOut {conversation_id: UUID, contact_phone_number: str, is_test: bool, credits_consumed: float, billed_responses: int, last_message_at: datetime}`; função `build_conversations_usage(session, tenant_id, date_from: date, date_to: date, limit: int, offset: int) -> list[ConversationUsageOut]`; rota `GET /api/v1/conversations/usage?from=&to=&limit=&offset=`. Consumida pela Task 5 (frontend).
- **Nota de nomenclatura**: `billed_responses` conta linhas de `messages` com `credits_consumed IS NOT NULL` (execuções cobradas) — não é o mesmo cálculo do `usage_last_30_days.agent_messages` do dashboard (que conta toda mensagem `sender_type=agent`).

- [ ] **Step 1: Escrever o teste do service (falha primeiro)** — criar `apps/api/tests/unit/test_conversations_usage_service.py`:

```python
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

from app.services.conversations_usage import build_conversations_usage

TENANT_ID = uuid.uuid4()


def _execute_result(rows: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


async def test_mapeia_linhas_agregadas_para_o_schema() -> None:
    session = AsyncMock()
    conv_id = uuid.uuid4()
    session.execute.return_value = _execute_result(
        [(conv_id, "5511999990001", False, 12.5, 3, datetime(2026, 7, 15, tzinfo=UTC))]
    )

    result = await build_conversations_usage(
        session, TENANT_ID, date(2026, 7, 1), date(2026, 7, 17), 50, 0
    )

    assert len(result) == 1
    assert result[0].conversation_id == conv_id
    assert result[0].contact_phone_number == "5511999990001"
    assert result[0].is_test is False
    assert result[0].credits_consumed == 12.5
    assert result[0].billed_responses == 3


async def test_sem_linhas_retorna_lista_vazia() -> None:
    session = AsyncMock()
    session.execute.return_value = _execute_result([])

    result = await build_conversations_usage(
        session, TENANT_ID, date(2026, 7, 1), date(2026, 7, 17), 50, 0
    )

    assert result == []


async def test_query_filtra_tenant_credits_consumed_not_null_e_periodo() -> None:
    session = AsyncMock()
    session.execute.return_value = _execute_result([])

    await build_conversations_usage(
        session, TENANT_ID, date(2026, 7, 1), date(2026, 7, 17), 50, 0
    )

    query = session.execute.call_args.args[0]
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "tenant_id" in compiled
    assert "credits_consumed IS NOT NULL" in compiled
    assert "2026-07-01" in compiled
    assert "2026-07-17" in compiled
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit/test_conversations_usage_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.conversations_usage'`

- [ ] **Step 3: Adicionar o schema** — em `apps/api/app/schemas/conversations.py`, adicionar ao final do arquivo:

```python
class ConversationUsageOut(BaseModel):
    conversation_id: uuid.UUID
    contact_phone_number: str
    is_test: bool
    credits_consumed: float
    billed_responses: int
    last_message_at: datetime
```

- [ ] **Step 4: Implementar o service** — criar `apps/api/app/services/conversations_usage.py`:

```python
"""Agregação de consumo de créditos por conversa — relatório do tenant."""

import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message
from app.schemas.conversations import ConversationUsageOut


async def build_conversations_usage(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    date_from: date,
    date_to: date,
    limit: int,
    offset: int,
) -> list[ConversationUsageOut]:
    range_start = datetime.combine(date_from, time.min, tzinfo=UTC)
    range_end = datetime.combine(date_to, time.max, tzinfo=UTC)

    rows = (
        await session.execute(
            select(
                Message.conversation_id,
                Conversation.contact_phone_number,
                Conversation.is_test,
                func.sum(Message.credits_consumed),
                func.count(Message.id),
                func.max(Message.created_at),
            )
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.tenant_id == tenant_id,
                Message.credits_consumed.is_not(None),
                Message.created_at >= range_start,
                Message.created_at <= range_end,
            )
            .group_by(
                Message.conversation_id, Conversation.contact_phone_number, Conversation.is_test
            )
            .order_by(func.sum(Message.credits_consumed).desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    return [
        ConversationUsageOut(
            conversation_id=conversation_id,
            contact_phone_number=contact_phone_number,
            is_test=is_test,
            credits_consumed=credits_consumed,
            billed_responses=billed_responses,
            last_message_at=last_message_at,
        )
        for (
            conversation_id,
            contact_phone_number,
            is_test,
            credits_consumed,
            billed_responses,
            last_message_at,
        ) in rows
    ]
```

- [ ] **Step 5: Rodar e ver passar**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit/test_conversations_usage_service.py -v`
Expected: PASS (3 testes)

- [ ] **Step 6: Escrever o teste da rota (falha primeiro)** — criar `apps/api/tests/unit/test_conversations_usage_routes.py`:

```python
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app

TENANT_ID = uuid.uuid4()


@pytest.fixture
def session():
    return AsyncMock()


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


def _execute_result(rows: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


class TestConversationsUsage:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).get(
            "/api/v1/conversations/usage", params={"from": "2026-07-01", "to": "2026-07-17"}
        )
        assert response.status_code == 401

    def test_agrega_por_conversa_ordenado_por_credito_desc(self, client, session) -> None:
        conv_a = uuid.uuid4()
        conv_b = uuid.uuid4()
        session.execute.return_value = _execute_result(
            [
                (conv_a, "5511999990001", False, 12.5, 3, datetime(2026, 7, 15, tzinfo=UTC)),
                (conv_b, "teste-abc123def456", True, 2.0, 1, datetime(2026, 7, 10, tzinfo=UTC)),
            ]
        )

        response = client.get(
            "/api/v1/conversations/usage", params={"from": "2026-07-01", "to": "2026-07-17"}
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        assert body[0]["conversation_id"] == str(conv_a)
        assert body[0]["credits_consumed"] == 12.5
        assert body[0]["billed_responses"] == 3
        assert body[1]["is_test"] is True

    def test_to_anterior_a_from_retorna_422(self, client) -> None:
        response = client.get(
            "/api/v1/conversations/usage", params={"from": "2026-07-17", "to": "2026-07-01"}
        )
        assert response.status_code == 422

    def test_sem_datas_retorna_422(self, client) -> None:
        response = client.get("/api/v1/conversations/usage")
        assert response.status_code == 422
```

- [ ] **Step 7: Rodar e ver falhar**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit/test_conversations_usage_routes.py -v`
Expected: FAIL — `404 Not Found`

- [ ] **Step 8: Adicionar a rota** — em `apps/api/app/api/v1/conversations.py`, o bloco de imports do topo (linhas 3-28) precisa ficar assim (note a ordem alfabética dos módulos de `app.services`: `conversations_usage` vem antes de `pricing`):

```python
import logging
import uuid
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.agents import (
    AgentsApiError,
    AgentsNetworkError,
    generate_conversation_summary,
    sync_conversation_context,
)
from app.clients.whatsapp import WhatsAppSendError, send_text_message
from app.core.crypto import decrypt_access_token
from app.models import Conversation, CreditTransaction, Message, Tenant, WhatsAppNumber
from app.schemas.conversations import (
    ConversationOut,
    ConversationStateUpdate,
    ConversationUsageOut,
    MessageOut,
    SendMessageRequest,
)
from app.services.conversations_usage import build_conversations_usage
from app.services.pricing import calcular_creditos, get_current_pricing_config
```

Adicionar a rota logo após `list_conversations`:

```python
@router.get("/usage")
async def get_conversations_usage(
    from_: date = Query(..., alias="from"),
    to: date = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ConversationUsageOut]:
    if to < from_:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'to' não pode ser anterior a 'from'",
        )
    return await build_conversations_usage(session, ctx.tenant_id, from_, to, limit, offset)
```

- [ ] **Step 9: Rodar e ver passar**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit/test_conversations_usage_routes.py -v`
Expected: PASS (4 testes)

- [ ] **Step 10: Rodar suíte inteira + lint**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run ruff check .`
Expected: PASS, All checks passed!

- [ ] **Step 11: Commit**

```bash
git add apps/api/app/schemas/conversations.py apps/api/app/services/conversations_usage.py \
  apps/api/app/api/v1/conversations.py apps/api/tests/unit/test_conversations_usage_service.py \
  apps/api/tests/unit/test_conversations_usage_routes.py
git commit -m "feat(api): consumo de créditos por conversa (GET /conversations/usage)"
```

---

### Task 5: `web` — aba "Consumo" em `/conversas`

**Files:**
- Create: `apps/web/src/components/ConversationsUsageReport.tsx`
- Modify: `apps/web/src/components/ConversationsPanel.tsx`
- Test: `apps/web/__tests__/ConversationsUsageReport.test.tsx` (novo), `apps/web/__tests__/ConversationsPanel.test.tsx` (nova asserção)

**Interfaces:**
- Consumes: `GET /api/v1/conversations/usage` (Task 4), `formatCredits`/`formatFullDateTime`/`formatPhone` de `@/lib/format`.
- Produces: componente `ConversationsUsageReport` (sem props), renderizado condicionalmente dentro de `ConversationsPanel`.

- [ ] **Step 1: Escrever o teste do relatório (falha primeiro)** — criar `apps/web/__tests__/ConversationsUsageReport.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationsUsageReport } from "@/components/ConversationsUsageReport";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("ConversationsUsageReport", () => {
  it("busca o período default de 30 dias ao carregar", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => [] });

    render(<ConversationsUsageReport />);

    await waitFor(() =>
      expect(
        mockedFetch.mock.calls.some(([path]) =>
          String(path).startsWith("conversations/usage?from="),
        ),
      ).toBe(true),
    );
  });

  it("mostra estado vazio quando não há consumo no período", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => [] });

    render(<ConversationsUsageReport />);

    await waitFor(() =>
      expect(screen.getByText("Nenhum consumo no período selecionado.")).toBeInTheDocument(),
    );
  });

  it("lista as conversas com créditos formatados e badge de teste", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          conversation_id: "c1",
          contact_phone_number: "5511999998888",
          is_test: false,
          credits_consumed: 12.5,
          billed_responses: 3,
          last_message_at: "2026-07-15T10:00:00Z",
        },
        {
          conversation_id: "c2",
          contact_phone_number: "teste-abc123def456",
          is_test: true,
          credits_consumed: 2,
          billed_responses: 1,
          last_message_at: "2026-07-10T10:00:00Z",
        },
      ],
    });

    render(<ConversationsUsageReport />);

    await waitFor(() => expect(screen.getByText("12,5")).toBeInTheDocument());
    expect(screen.getByText("teste")).toBeInTheDocument();
  });

  it("trocar para o preset de 7 dias refaz a busca com o novo intervalo", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => [] });

    render(<ConversationsUsageReport />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    mockedFetch.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "7 dias" }));

    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
  });

  it("nunca menciona tokens na tela", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          conversation_id: "c1",
          contact_phone_number: "5511999998888",
          is_test: false,
          credits_consumed: 5,
          billed_responses: 2,
          last_message_at: "2026-07-15T10:00:00Z",
        },
      ],
    });

    const { container } = render(<ConversationsUsageReport />);

    await waitFor(() => expect(screen.getByText("5")).toBeInTheDocument());
    expect(container.textContent?.toLowerCase()).not.toContain("token");
  });
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/web && pnpm vitest run __tests__/ConversationsUsageReport.test.tsx`
Expected: FAIL — `Cannot find module '@/components/ConversationsUsageReport'`

- [ ] **Step 3: Implementar o componente** — criar `apps/web/src/components/ConversationsUsageReport.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import { formatCredits, formatFullDateTime, formatPhone } from "@/lib/format";

type UsageRow = {
  conversation_id: string;
  contact_phone_number: string;
  is_test: boolean;
  credits_consumed: number;
  billed_responses: number;
  last_message_at: string;
};

type Preset = "7" | "30" | "90" | "custom";

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function rangeForPreset(preset: Preset): { from: string; to: string } {
  const to = new Date();
  const from = new Date();
  const days = preset === "custom" ? 30 : Number(preset);
  from.setDate(from.getDate() - days);
  return { from: isoDate(from), to: isoDate(to) };
}

export function ConversationsUsageReport() {
  const [preset, setPreset] = useState<Preset>("30");
  const [range, setRange] = useState(() => rangeForPreset("30"));
  const [rows, setRows] = useState<UsageRow[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    async function load() {
      setLoaded(false);
      try {
        const response = await backendFetch(
          `conversations/usage?from=${range.from}&to=${range.to}`,
        );
        if (response.ok) {
          setRows(await response.json());
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, [range]);

  function selectPreset(next: Preset) {
    setPreset(next);
    if (next !== "custom") {
      setRange(rangeForPreset(next));
    }
  }

  return (
    <div className="flex flex-1 flex-col overflow-y-auto px-8 py-6">
      <div className="flex flex-wrap items-center gap-3">
        {(["7", "30", "90"] as Preset[]).map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => selectPreset(p)}
            aria-pressed={preset === p}
            className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
              preset === p ? "bg-ink text-ground" : "text-muted hover:text-ink"
            }`}
          >
            {p} dias
          </button>
        ))}
        <button
          type="button"
          onClick={() => setPreset("custom")}
          aria-pressed={preset === "custom"}
          className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
            preset === "custom" ? "bg-ink text-ground" : "text-muted hover:text-ink"
          }`}
        >
          Personalizado
        </button>
        {preset === "custom" && (
          <div className="flex items-center gap-2 text-sm text-ink">
            <input
              type="date"
              value={range.from}
              onChange={(event) => setRange((prev) => ({ ...prev, from: event.target.value }))}
              className="rounded border border-line bg-surface px-2 py-1 text-sm"
            />
            <span className="text-muted">até</span>
            <input
              type="date"
              value={range.to}
              onChange={(event) => setRange((prev) => ({ ...prev, to: event.target.value }))}
              className="rounded border border-line bg-surface px-2 py-1 text-sm"
            />
          </div>
        )}
      </div>

      <table className="mt-6 w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line text-xs uppercase tracking-[0.1em] text-muted">
            <th className="py-2">Contato</th>
            <th className="py-2">Créditos consumidos</th>
            <th className="py-2">Respostas do agente</th>
            <th className="py-2">Última atividade</th>
          </tr>
        </thead>
        <tbody>
          {!loaded ? (
            <tr>
              <td className="py-4 text-sm text-muted" colSpan={4}>
                Carregando...
              </td>
            </tr>
          ) : rows.length === 0 ? (
            <tr>
              <td className="py-4 text-sm text-muted" colSpan={4}>
                Nenhum consumo no período selecionado.
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr key={row.conversation_id} className="border-b border-line">
                <td className="py-3">
                  {formatPhone(row.contact_phone_number)}
                  {row.is_test && (
                    <span className="ml-2 rounded-sm bg-brass-soft px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-brass">
                      teste
                    </span>
                  )}
                </td>
                <td className="py-3 font-mono">{formatCredits(row.credits_consumed)}</td>
                <td className="py-3">{row.billed_responses}</td>
                <td className="py-3 text-muted">{formatFullDateTime(row.last_message_at)}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/web && pnpm vitest run __tests__/ConversationsUsageReport.test.tsx`
Expected: PASS (5 testes)

- [ ] **Step 5: Adicionar a asserção que falha em `ConversationsPanel.test.tsx`** — no final do `describe("ConversationsPanel — abas", ...)` em `apps/web/__tests__/ConversationsPanel.test.tsx`, adicionar:

```tsx
  it("aba Consumo mostra o relatório e não busca conversations?origin=", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(<ConversationsPanel pollMs={0} />);
    await waitFor(() => expect(backendFetchMock).toHaveBeenCalled());
    backendFetchMock.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "Consumo" }));

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(([p]) => String(p).startsWith("conversations/usage")),
      ).toBe(true),
    );
    expect(backendFetchMock.mock.calls.some(([p]) => String(p).includes("origin="))).toBe(false);
  });
```

- [ ] **Step 6: Rodar e ver falhar**

Run: `cd apps/web && pnpm vitest run __tests__/ConversationsPanel.test.tsx`
Expected: FAIL — não existe botão com nome "Consumo"

- [ ] **Step 7: Reestruturar `ConversationsPanel.tsx`** — substituir o conteúdo inteiro do arquivo por:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type { Conversation } from "@/lib/types";

import { ConversationList } from "./ConversationList";
import { ConversationsUsageReport } from "./ConversationsUsageReport";
import { ConversationThread } from "./ConversationThread";
import { TestConversationThread } from "./TestConversationThread";

type Tab = "real" | "test" | "usage";

export function ConversationsPanel({
  pollMs = 5000,
  initialOrigin = "real",
}: {
  pollMs?: number;
  initialOrigin?: "real" | "test";
}) {
  const [tab, setTab] = useState<Tab>(initialOrigin);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const loadConversations = useCallback(async () => {
    if (tab === "usage") return;
    try {
      const response = await backendFetch(`conversations?origin=${tab}`);
      if (response.ok) {
        setConversations(await response.json());
      }
    } catch {
      // rede indisponível: mantém a lista atual e tenta no próximo ciclo
    } finally {
      setLoaded(true);
    }
  }, [tab]);

  useEffect(() => {
    if (tab === "usage") return;
    setLoaded(false);
    void loadConversations();
    if (!pollMs) {
      return;
    }
    const interval = setInterval(() => void loadConversations(), pollMs);
    return () => clearInterval(interval);
  }, [loadConversations, pollMs, tab]);

  const selected = conversations.find((c) => c.id === selectedId) ?? null;

  const handleConversationUpdate = (updated: Conversation) => {
    setConversations((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
  };

  const switchTab = (next: Tab) => {
    if (next === tab) return;
    setTab(next);
    setSelectedId(null);
    setConversations([]);
  };

  const createTestConversation = async () => {
    if (creating) return;
    setCreating(true);
    try {
      const response = await backendFetch("test-conversations", { method: "POST" });
      if (response.ok) {
        const created: Conversation = await response.json();
        setConversations((prev) => [created, ...prev]);
        setSelectedId(created.id);
      }
    } finally {
      setCreating(false);
    }
  };

  const handleDeleted = (id: string) => {
    setConversations((prev) => prev.filter((c) => c.id !== id));
    setSelectedId(null);
  };

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="flex items-baseline justify-between border-b border-line px-5 py-4">
        <h1 className="font-display text-xl font-semibold">Conversas</h1>
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => switchTab("real")}
            aria-pressed={tab === "real"}
            className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
              tab === "real" ? "bg-ink text-ground" : "text-muted hover:text-ink"
            }`}
          >
            Conversas
          </button>
          <button
            type="button"
            onClick={() => switchTab("test")}
            aria-pressed={tab === "test"}
            className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
              tab === "test" ? "bg-ink text-ground" : "text-muted hover:text-ink"
            }`}
          >
            Testes
          </button>
          <button
            type="button"
            onClick={() => switchTab("usage")}
            aria-pressed={tab === "usage"}
            className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
              tab === "usage" ? "bg-ink text-ground" : "text-muted hover:text-ink"
            }`}
          >
            Consumo
          </button>
        </div>
      </header>

      {tab === "usage" ? (
        <ConversationsUsageReport />
      ) : (
        <div className="flex min-h-0 min-w-0 flex-1">
          <aside className="flex w-80 shrink-0 flex-col border-r border-line">
            <div className="flex items-center justify-end px-5 py-3">
              <span className="font-mono text-xs text-muted">{conversations.length}</span>
            </div>
            {tab === "test" ? (
              <button
                type="button"
                onClick={() => void createTestConversation()}
                disabled={creating}
                className="border-b border-line px-5 py-3 text-left text-sm font-medium text-accent transition-colors hover:bg-surface/60 disabled:opacity-50"
              >
                {creating ? "Criando…" : "Nova conversa de teste"}
              </button>
            ) : null}
            <ConversationList
              conversations={conversations}
              loaded={loaded}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
          </aside>

          <section className="flex min-w-0 flex-1 flex-col bg-surface/40">
            {selected ? (
              selected.is_test ? (
                <TestConversationThread
                  key={selected.id}
                  conversation={selected}
                  onDeleted={() => handleDeleted(selected.id)}
                />
              ) : (
                <ConversationThread
                  key={selected.id}
                  conversation={selected}
                  onConversationUpdate={handleConversationUpdate}
                />
              )
            ) : (
              <div className="flex flex-1 items-center justify-center p-8">
                <p className="max-w-xs text-center text-sm leading-relaxed text-muted">
                  {tab === "test"
                    ? "Crie uma conversa de teste para experimentar os agentes sem WhatsApp."
                    : "Selecione uma conversa para acompanhar o atendimento."}
                </p>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 8: Rodar e ver passar**

Run: `cd apps/web && pnpm vitest run __tests__/ConversationsPanel.test.tsx`
Expected: PASS (4 testes — os 3 originais + o novo)

- [ ] **Step 9: Rodar a suíte inteira do web + lint**

Run: `cd apps/web && pnpm test && pnpm lint`
Expected: PASS, sem erros novos

- [ ] **Step 10: Commit**

```bash
git add apps/web/src/components/ConversationsUsageReport.tsx apps/web/src/components/ConversationsPanel.tsx \
  apps/web/__tests__/ConversationsUsageReport.test.tsx apps/web/__tests__/ConversationsPanel.test.tsx
git commit -m "feat(web): aba Consumo em /conversas — relatório de créditos por conversa"
```

---

### Task 6: `api` — saldo/consumo de clientes finais (`GET /end-customer-billing/customers`)

**Files:**
- Modify: `apps/api/app/schemas/end_customer_billing.py`
- Modify: `apps/api/app/services/end_customer_billing.py`
- Modify: `apps/api/app/api/v1/end_customer_billing.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_service.py`, `apps/api/tests/unit/test_end_customer_billing_customers_routes.py` (novo)

**Interfaces:**
- Produces: schema `EndCustomerSummaryOut {contact_phone_number: str, credit_balance: float, total_purchased: float, total_consumed: float}`; função `list_customers(session, tenant_id, limit, offset) -> list[EndCustomerSummaryOut]`; rota `GET /api/v1/end-customer-billing/customers?limit=&offset=`. Consumida pela Task 7 (frontend).

- [ ] **Step 1: Escrever o teste do service (falha primeiro)** — em `apps/api/tests/unit/test_end_customer_billing_service.py`, trocar o import:

```python
from app.services.end_customer_billing import (
    BillingNotConfiguredError,
    InvalidPackageError,
    StripeApiError,
    create_end_customer_checkout_session,
    process_end_customer_checkout_completed,
)
```
por:
```python
from app.services.end_customer_billing import (
    BillingNotConfiguredError,
    InvalidPackageError,
    StripeApiError,
    create_end_customer_checkout_session,
    list_customers,
    process_end_customer_checkout_completed,
)
```

Adicionar ao final do arquivo:

```python
class TestListCustomers:
    async def test_agrega_saldo_compra_e_consumo_por_contato(self) -> None:
        session = AsyncMock()
        result = MagicMock()
        result.all.return_value = [
            ("5511999990001", 120.0, 500.0, -380.0),
        ]
        session.execute.return_value = result

        customers = await list_customers(session, TENANT_ID, 50, 0)

        assert len(customers) == 1
        assert customers[0].contact_phone_number == "5511999990001"
        assert customers[0].credit_balance == 120.0
        assert customers[0].total_purchased == 500.0
        assert customers[0].total_consumed == 380.0  # abs()

    async def test_sem_clientes_retorna_lista_vazia(self) -> None:
        session = AsyncMock()
        result = MagicMock()
        result.all.return_value = []
        session.execute.return_value = result

        customers = await list_customers(session, TENANT_ID, 50, 0)

        assert customers == []

    async def test_query_filtra_por_tenant_id(self) -> None:
        session = AsyncMock()
        result = MagicMock()
        result.all.return_value = []
        session.execute.return_value = result

        await list_customers(session, TENANT_ID, 50, 0)

        query = session.execute.call_args.args[0]
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
        assert "tenant_id" in compiled
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit/test_end_customer_billing_service.py::TestListCustomers -v`
Expected: FAIL — `ImportError: cannot import name 'list_customers'`

- [ ] **Step 3: Adicionar o schema** — em `apps/api/app/schemas/end_customer_billing.py`, adicionar ao final do arquivo:

```python
class EndCustomerSummaryOut(BaseModel):
    contact_phone_number: str
    credit_balance: float
    total_purchased: float
    total_consumed: float
```

- [ ] **Step 4: Implementar `list_customers`** — em `apps/api/app/services/end_customer_billing.py`, o bloco de imports (linhas 8-28) precisa ficar assim:

```python
import asyncio
import logging
import uuid
from datetime import UTC, datetime

import stripe
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.whatsapp import WhatsAppSendError, send_text_message
from app.core.config import settings
from app.core.crypto import decrypt_access_token, decrypt_tenant_secret
from app.models import (
    Conversation,
    EndCustomerBalance,
    EndCustomerCreditPackage,
    EndCustomerCreditTransaction,
    Message,
    TenantBillingSettings,
    WhatsAppNumber,
)
from app.schemas.end_customer_billing import EndCustomerSummaryOut
```

Adicionar ao final do arquivo:

```python
async def list_customers(
    session: AsyncSession, tenant_id: uuid.UUID, limit: int, offset: int
) -> list[EndCustomerSummaryOut]:
    """Saldo atual + total comprado/consumido por cliente final do tenant."""
    purchased = func.coalesce(
        func.sum(
            case(
                (
                    EndCustomerCreditTransaction.type == "purchase",
                    EndCustomerCreditTransaction.amount_credits,
                ),
                else_=0,
            )
        ),
        0,
    )
    consumed = func.coalesce(
        func.sum(
            case(
                (
                    EndCustomerCreditTransaction.type == "consumption",
                    EndCustomerCreditTransaction.amount_credits,
                ),
                else_=0,
            )
        ),
        0,
    )
    rows = (
        await session.execute(
            select(
                EndCustomerBalance.contact_phone_number,
                EndCustomerBalance.credit_balance,
                purchased,
                consumed,
            )
            .outerjoin(
                EndCustomerCreditTransaction,
                (EndCustomerCreditTransaction.tenant_id == EndCustomerBalance.tenant_id)
                & (
                    EndCustomerCreditTransaction.contact_phone_number
                    == EndCustomerBalance.contact_phone_number
                ),
            )
            .where(EndCustomerBalance.tenant_id == tenant_id)
            .group_by(EndCustomerBalance.contact_phone_number, EndCustomerBalance.credit_balance)
            .order_by(func.abs(consumed).desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    return [
        EndCustomerSummaryOut(
            contact_phone_number=contact_phone_number,
            credit_balance=credit_balance,
            total_purchased=total_purchased,
            total_consumed=abs(total_consumed),
        )
        for contact_phone_number, credit_balance, total_purchased, total_consumed in rows
    ]
```

- [ ] **Step 5: Rodar e ver passar**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit/test_end_customer_billing_service.py -v`
Expected: PASS (todos)

- [ ] **Step 6: Escrever o teste da rota (falha primeiro)** — criar `apps/api/tests/unit/test_end_customer_billing_customers_routes.py`:

```python
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.end_customer_billing as end_customer_billing_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app
from app.schemas.end_customer_billing import EndCustomerSummaryOut

TENANT_ID = uuid.uuid4()


@pytest.fixture
def session():
    return AsyncMock()


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


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/end-customer-billing/customers")
    assert response.status_code == 401


def test_lista_clientes_delegando_ao_service(client, session, monkeypatch) -> None:
    fake = AsyncMock(
        return_value=[
            EndCustomerSummaryOut(
                contact_phone_number="5511999990001",
                credit_balance=120.0,
                total_purchased=500.0,
                total_consumed=380.0,
            )
        ]
    )
    monkeypatch.setattr(end_customer_billing_module, "list_customers", fake)

    response = client.get("/api/v1/end-customer-billing/customers")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["contact_phone_number"] == "5511999990001"
    assert body[0]["total_consumed"] == 380.0
    fake.assert_awaited_once_with(session, TENANT_ID, 50, 0)


def test_respeita_limit_e_offset(client, session, monkeypatch) -> None:
    fake = AsyncMock(return_value=[])
    monkeypatch.setattr(end_customer_billing_module, "list_customers", fake)

    response = client.get("/api/v1/end-customer-billing/customers?limit=10&offset=5")

    assert response.status_code == 200
    fake.assert_awaited_once_with(session, TENANT_ID, 10, 5)
```

- [ ] **Step 7: Rodar e ver falhar**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit/test_end_customer_billing_customers_routes.py -v`
Expected: FAIL — `404 Not Found`

- [ ] **Step 8: Adicionar a rota** — em `apps/api/app/api/v1/end_customer_billing.py`, o bloco de imports do topo precisa ficar assim:

```python
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.core.crypto import encrypt_tenant_secret
from app.models import EndCustomerCreditPackage, EndCustomerCreditTransaction, TenantBillingSettings
from app.schemas.end_customer_billing import (
    EndCustomerCreditPackageIn,
    EndCustomerCreditPackageOut,
    EndCustomerCreditPackageUpdate,
    EndCustomerSummaryOut,
    TenantBillingSettingsOut,
    TenantBillingSettingsUpdate,
)
from app.services.end_customer_billing import list_customers
```

Adicionar a rota ao final do arquivo:

```python
@router.get("/customers")
async def list_end_customers(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[EndCustomerSummaryOut]:
    return await list_customers(session, ctx.tenant_id, limit, offset)
```

- [ ] **Step 9: Rodar e ver passar**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit/test_end_customer_billing_customers_routes.py -v`
Expected: PASS (3 testes)

- [ ] **Step 10: Rodar suíte inteira + lint**

Run: `cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run ruff check .`
Expected: PASS, All checks passed!

- [ ] **Step 11: Commit**

```bash
git add apps/api/app/schemas/end_customer_billing.py apps/api/app/services/end_customer_billing.py \
  apps/api/app/api/v1/end_customer_billing.py apps/api/tests/unit/test_end_customer_billing_service.py \
  apps/api/tests/unit/test_end_customer_billing_customers_routes.py
git commit -m "feat(api): saldo e consumo dos clientes finais por contato (GET /end-customer-billing/customers)"
```

---

### Task 7: `web` — lista de clientes finais em `/configuracoes/cobranca-clientes`

**Files:**
- Create: `apps/web/src/components/EndCustomerList.tsx`
- Modify: `apps/web/src/components/EndCustomerBillingPanel.tsx`
- Test: `apps/web/__tests__/EndCustomerList.test.tsx` (novo), `apps/web/__tests__/EndCustomerBillingPanel.test.tsx` (novas asserções)

**Interfaces:**
- Consumes: `GET /api/v1/end-customer-billing/customers` (Task 6), `formatCredits`/`formatPhone` de `@/lib/format`.
- Produces: componente `EndCustomerList` (sem props), renderizado dentro de `EndCustomerBillingPanel` só quando `settings.enabled === true`.

- [ ] **Step 1: Escrever o teste do componente (falha primeiro)** — criar `apps/web/__tests__/EndCustomerList.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EndCustomerList } from "@/components/EndCustomerList";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("EndCustomerList", () => {
  it("mostra estado vazio quando não há clientes", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => [] });

    render(<EndCustomerList />);

    await waitFor(() =>
      expect(screen.getByText("Nenhum cliente comprou créditos ainda.")).toBeInTheDocument(),
    );
  });

  it("lista clientes com saldo, comprado e consumido formatados", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          contact_phone_number: "5511999990001",
          credit_balance: 120,
          total_purchased: 500,
          total_consumed: 380,
        },
      ],
    });

    render(<EndCustomerList />);

    await waitFor(() => expect(screen.getByText("+55 11 99999-0001")).toBeInTheDocument());
    expect(screen.getByText("120")).toBeInTheDocument();
    expect(screen.getByText("500")).toBeInTheDocument();
    expect(screen.getByText("380")).toBeInTheDocument();
  });

  it("nunca menciona tokens na tela", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          contact_phone_number: "5511999990001",
          credit_balance: 120,
          total_purchased: 500,
          total_consumed: 380,
        },
      ],
    });

    const { container } = render(<EndCustomerList />);

    await waitFor(() => expect(screen.getByText("120")).toBeInTheDocument());
    expect(container.textContent?.toLowerCase()).not.toContain("token");
  });
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/web && pnpm vitest run __tests__/EndCustomerList.test.tsx`
Expected: FAIL — `Cannot find module '@/components/EndCustomerList'`

- [ ] **Step 3: Implementar o componente** — criar `apps/web/src/components/EndCustomerList.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import { formatCredits, formatPhone } from "@/lib/format";

type Customer = {
  contact_phone_number: string;
  credit_balance: number;
  total_purchased: number;
  total_consumed: number;
};

export function EndCustomerList() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const response = await backendFetch("end-customer-billing/customers");
        if (response.ok) {
          setCustomers(await response.json());
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, []);

  return (
    <div className="mt-8">
      <h2 className="font-display text-lg font-semibold text-ink">Clientes finais</h2>
      {!loaded ? (
        <p className="mt-3 text-sm text-muted">Carregando...</p>
      ) : customers.length === 0 ? (
        <p className="mt-3 text-sm text-muted">Nenhum cliente comprou créditos ainda.</p>
      ) : (
        <table className="mt-4 w-full max-w-md text-left text-sm">
          <thead>
            <tr className="border-b border-line text-xs uppercase tracking-[0.1em] text-muted">
              <th className="py-2">Contato</th>
              <th className="py-2">Saldo</th>
              <th className="py-2">Comprado</th>
              <th className="py-2">Consumido</th>
            </tr>
          </thead>
          <tbody>
            {customers.map((c) => (
              <tr key={c.contact_phone_number} className="border-b border-line">
                <td className="py-3">{formatPhone(c.contact_phone_number)}</td>
                <td className="py-3 font-mono">{formatCredits(c.credit_balance)}</td>
                <td className="py-3 font-mono text-muted">{formatCredits(c.total_purchased)}</td>
                <td className="py-3 font-mono text-muted">{formatCredits(c.total_consumed)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/web && pnpm vitest run __tests__/EndCustomerList.test.tsx`
Expected: PASS (3 testes)

- [ ] **Step 5: Escrever as novas asserções em `EndCustomerBillingPanel.test.tsx` (falham primeiro)** — trocar a assinatura e corpo de `mockLoad`:

```tsx
function mockLoad(settings: unknown, packages: unknown[] = [], customers: unknown[] = []) {
  mockedFetch.mockImplementation(async (path: string) => {
    if (path === "end-customer-billing/settings") {
      return { ok: true, json: async () => settings };
    }
    if (path === "end-customer-billing/packages") {
      return { ok: true, json: async () => packages };
    }
    if (path === "end-customer-billing/customers") {
      return { ok: true, json: async () => customers };
    }
    return { ok: false, json: async () => null };
  });
}
```

Adicionar ao final do `describe`:

```tsx
  it("mostra a lista de clientes finais quando a cobrança está habilitada", async () => {
    mockLoad(
      {
        enabled: true,
        billing_mode: "credits",
        stripe_secret_key_configured: true,
        stripe_webhook_secret_configured: true,
        end_customer_tokens_per_credit: 500,
      },
      [],
      [
        {
          contact_phone_number: "5511999990001",
          credit_balance: 120,
          total_purchased: 500,
          total_consumed: 380,
        },
      ],
    );

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByText("Clientes finais")).toBeInTheDocument());
    expect(screen.getByText("+55 11 99999-0001")).toBeInTheDocument();
  });

  it("não busca clientes finais quando a cobrança está desligada", async () => {
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      stripe_secret_key_configured: false,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByLabelText(/cobrar meus clientes/i)).not.toBeChecked());
    expect(screen.queryByText("Clientes finais")).not.toBeInTheDocument();
    expect(
      mockedFetch.mock.calls.some(([p]) => p === "end-customer-billing/customers"),
    ).toBe(false);
  });
```

- [ ] **Step 6: Rodar e ver falhar**

Run: `cd apps/web && pnpm vitest run __tests__/EndCustomerBillingPanel.test.tsx`
Expected: FAIL — texto "Clientes finais" não encontrado

- [ ] **Step 7: Ligar o componente** — em `apps/web/src/components/EndCustomerBillingPanel.tsx`, o arquivo não tem nenhum import relativo hoje — trocar:

```tsx
import { backendFetch } from "@/lib/client-api";

type Settings = {
```
por:
```tsx
import { backendFetch } from "@/lib/client-api";

import { EndCustomerList } from "./EndCustomerList";

type Settings = {
```

Adicionar, imediatamente antes do `</div>` que fecha o container `flex-1 overflow-y-auto px-8 py-6` (depois do `<form onSubmit={handleCreatePackage} ...>...</form>`):

```tsx
        {settings.enabled && <EndCustomerList />}
```

- [ ] **Step 8: Rodar e ver passar**

Run: `cd apps/web && pnpm vitest run __tests__/EndCustomerBillingPanel.test.tsx`
Expected: PASS (todos)

- [ ] **Step 9: Rodar a suíte inteira do web + lint**

Run: `cd apps/web && pnpm test && pnpm lint`
Expected: PASS, sem erros novos

- [ ] **Step 10: Commit**

```bash
git add apps/web/src/components/EndCustomerList.tsx apps/web/src/components/EndCustomerBillingPanel.tsx \
  apps/web/__tests__/EndCustomerList.test.tsx apps/web/__tests__/EndCustomerBillingPanel.test.tsx
git commit -m "feat(web): lista de saldo/consumo dos clientes finais em /configuracoes/cobranca-clientes"
```

---

### Task 8: Verificação final, smoke test manual e CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Suíte completa dos 3 apps tocados**

Run:
```bash
cd apps/worker && UV_PROJECT_ENVIRONMENT=/tmp/venv-worker uv run pytest tests/unit && UV_PROJECT_ENVIRONMENT=/tmp/venv-worker uv run ruff check .
cd apps/api && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run pytest tests/unit && UV_PROJECT_ENVIRONMENT=/tmp/venv-api uv run ruff check .
cd apps/web && pnpm test && pnpm lint && pnpm build
```
Expected: tudo verde.

- [ ] **Step 2: Smoke test manual dos 3 endpoints novos contra o Postgres local** — os testes unitários mockam `session.execute`; isso não garante que o SQL gerado (em especial as agregações com `case`/`group_by`/`order_by` das Tasks 4 e 6) seja sintaticamente válido no Postgres real. Com os containers rodando (`docker compose up -d`) e logado como o tenant demo (`admin@demo.com`/`segredo123`, ver sessão anterior desta conversa para o fluxo de login):

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login -H "Content-Type: application/json" -d '{"email":"admin@demo.com","password":"segredo123"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s http://localhost:8000/api/v1/billing/transactions -H "Authorization: Bearer $TOKEN"
curl -s "http://localhost:8000/api/v1/conversations/usage?from=2026-06-01&to=2026-07-17" -H "Authorization: Bearer $TOKEN"
curl -s http://localhost:8000/api/v1/end-customer-billing/customers -H "Authorization: Bearer $TOKEN"
```
Expected: os 3 devolvem `200` com uma lista JSON (vazia ou não) — nenhum `500`. Se algum devolver `500`, ler o traceback nos logs do container `api` (`docker compose logs api --tail 50`) e corrigir a query antes de prosseguir.

- [ ] **Step 3: Verificação visual no browser** — seguir a skill `run`/`verify` do projeto: abrir `http://localhost:3000/creditos` (extrato aparece abaixo do saldo/pacotes), `http://localhost:3000/conversas` (clicar na aba "Consumo", trocar os presets de data), e `http://localhost:3000/configuracoes/cobranca-clientes` (com a cobrança habilitada, a lista de clientes finais aparece). Confirmar visualmente que nenhuma tela menciona "token".

- [ ] **Step 4: Atualizar CLAUDE.md** — na seção "Frontend (`apps/web`)", no bullet de `/creditos`, acrescentar a menção ao extrato; no bullet do Painel de Conversas, acrescentar a aba "Consumo"; na seção "Cobrança do cliente final", acrescentar a lista de clientes finais. Registrar que a Etapa 5 (Visibilidade) do plano de wallet unificada está implementada, referenciando `docs/superpowers/specs/2026-07-17-visibilidade-creditos-tenant-design.md`.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: registra a visibilidade de créditos do tenant (etapa 5) no CLAUDE.md"
```

## Self-Review

- **Cobertura da spec**: Peça 1 (Task 2+3), Peça 2 (Task 4+5), Peça 3 (Task 6+7), regra "nunca tokens" (Task 1 + asserção dedicada em cada teste de componente novo), limitação do resumo sob demanda documentada na spec (não requer código, só foi citada). Nenhum requisito da spec ficou sem task.
- **Placeholders**: nenhum "TBD"/"adicionar validação" — todo step tem código completo.
- **Consistência de tipos**: `ConversationUsageOut.billed_responses` (schema) ↔ `build_conversations_usage` (service, retorna o campo com esse nome) ↔ `ConversationsUsageReport.tsx` (`UsageRow.billed_responses`) — nome consistente nas 3 camadas. `EndCustomerSummaryOut` ↔ `list_customers` ↔ `EndCustomerList.tsx` (`Customer`) — idem.
