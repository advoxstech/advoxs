# Tutorial de primeira abertura — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wizard de boas-vindas mostrado uma única vez por tenant (WhatsApp Business + Stripe da cobrança, skippável pra aba Testes), com gate no `/inicio`.

**Architecture:** Coluna `tenants.onboarding_completed_at` (migration 0012, com backfill — tenants existentes nunca veem o tutorial) + router `onboarding.py` (`GET` estado / `POST complete` idempotente). No web: `OnboardingGate` client no `/inicio` (fail-open), página `/boas-vindas` com wizard de 3 passos (webhook-config copiável no passo 2), e `?aba=testes` no `/conversas` via prop `initialOrigin`.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (api), Next.js 15 App Router + Vitest (web).

**Spec:** `docs/superpowers/specs/2026-07-16-tutorial-primeira-abertura-design.md`

## Global Constraints

- Migration é a **0012** (`down_revision = "0011"`), com **backfill** no `upgrade()`: `UPDATE tenants SET onboarding_completed_at = now()`.
- NÃO espelhar a coluna em `apps/worker/app/tables.py` (o worker não a lê — decisão registrada no spec).
- `POST /onboarding/complete` é idempotente: re-POST não altera o timestamp original.
- Gate é **fail-open**: erro de rede/5xx na checagem → renderiza o dashboard normalmente.
- O POST de completar dispara em QUALQUER saída do wizard (Concluir, Configurar agora, Pular) ANTES de navegar; se o POST falhar, navega mesmo assim.
- Navegações do wizard usam `window.location.assign` (navegação dura — consistente com o padrão do auto-login e re-executa o gate limpo).
- Textos exatos: botões "Começar", "Configurar WhatsApp agora", "Próximo", "Configurar cobrança", "Concluir", link "Pular e testar os agentes"; título "Bem-vindo à Advoxs".
- `?aba=testes` → `initialOrigin="test"`; qualquer outro valor (ou ausência) → `"real"`.
- Comandos: api → `cd apps/api && uv run pytest tests/unit -q` + `uv run ruff check . && uv run ruff format --check .`; web → `cd apps/web && pnpm test` + `pnpm lint` (2 warnings de `<img>` pré-existentes aceitos).

---

### Task 1: api — migration 0012 + rotas de onboarding

**Files:**
- Create: `apps/api/alembic/versions/0012_tenant_onboarding_completed.py`, `apps/api/app/api/v1/onboarding.py`, `apps/api/app/schemas/onboarding.py`, `apps/api/tests/unit/test_onboarding_routes.py`
- Modify: `apps/api/app/models/tenant.py` (coluna), `apps/api/app/api/v1/router.py` (registrar)

**Interfaces:**
- Produces: `GET /api/v1/onboarding` → `{"completed": bool}`; `POST /api/v1/onboarding/complete` → 204. Tasks 2-3 consomem.

- [ ] **Step 1: Testes (falhando)**

Criar `apps/api/tests/unit/test_onboarding_routes.py` (mesmo desenho de fixtures dos arquivos vizinhos — session `AsyncMock`, overrides de `get_current_tenant`/`get_tenant_session`):

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


def _tenant(completed_at=None) -> SimpleNamespace:
    return SimpleNamespace(id=TENANT_ID, onboarding_completed_at=completed_at)


@pytest.fixture
def session():
    mock = AsyncMock()
    mock.add = MagicMock()
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


def test_get_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/onboarding")
    assert response.status_code == 401


class TestGetOnboarding:
    def test_nao_completado(self, client, session) -> None:
        session.get.return_value = _tenant(completed_at=None)

        response = client.get("/api/v1/onboarding")

        assert response.status_code == 200
        assert response.json() == {"completed": False}

    def test_completado(self, client, session) -> None:
        session.get.return_value = _tenant(completed_at=datetime(2026, 7, 16, tzinfo=UTC))

        response = client.get("/api/v1/onboarding")

        assert response.json() == {"completed": True}


class TestCompleteOnboarding:
    def test_seta_timestamp_e_retorna_204(self, client, session) -> None:
        tenant = _tenant(completed_at=None)
        session.get.return_value = tenant

        response = client.post("/api/v1/onboarding/complete")

        assert response.status_code == 204
        assert tenant.onboarding_completed_at is not None
        session.commit.assert_awaited()

    def test_idempotente_nao_altera_timestamp_original(self, client, session) -> None:
        original = datetime(2026, 7, 1, tzinfo=UTC)
        tenant = _tenant(completed_at=original)
        session.get.return_value = tenant

        response = client.post("/api/v1/onboarding/complete")

        assert response.status_code == 204
        assert tenant.onboarding_completed_at == original
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_onboarding_routes.py -q`
Expected: FAIL — 404 nas rotas.

- [ ] **Step 3: Migration + modelo + schema + router**

`apps/api/alembic/versions/0012_tenant_onboarding_completed.py`:

```python
"""onboarding_completed_at em tenants

Tutorial de primeira abertura: NULL = tenant ainda não viu o wizard de
boas-vindas. Backfill marca todos os tenants existentes como completados —
só conta criada depois deste deploy vê o tutorial.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-16
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE tenants SET onboarding_completed_at = now()")


def downgrade() -> None:
    op.drop_column("tenants", "onboarding_completed_at")
```

`apps/api/app/models/tenant.py` — adicionar após `status`:

```python
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

(Conferir imports: `datetime` e `DateTime` — adicionar se ausentes.)

`apps/api/app/schemas/onboarding.py`:

```python
from pydantic import BaseModel


class OnboardingOut(BaseModel):
    completed: bool
```

`apps/api/app/api/v1/onboarding.py`:

```python
"""Tutorial de primeira abertura — flag por tenant, mostrado uma única vez."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.models import Tenant
from app.schemas.onboarding import OnboardingOut

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("")
async def get_onboarding(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> OnboardingOut:
    tenant = await session.get(Tenant, ctx.tenant_id)
    return OnboardingOut(completed=tenant.onboarding_completed_at is not None)


@router.post("/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_onboarding(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Idempotente: qualquer saída do wizard completa; re-POST não altera."""
    tenant = await session.get(Tenant, ctx.tenant_id)
    if tenant.onboarding_completed_at is None:
        tenant.onboarding_completed_at = datetime.now(UTC)
        await session.commit()
```

Registrar em `apps/api/app/api/v1/router.py` seguindo o padrão dos includes existentes (`from app.api.v1 import onboarding` + include do `onboarding.router`).

- [ ] **Step 4: Rodar tudo + lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS e lint limpo.

- [ ] **Step 5: Commit**

```bash
git add apps/api/alembic/versions/0012_tenant_onboarding_completed.py apps/api/app/models/tenant.py apps/api/app/schemas/onboarding.py apps/api/app/api/v1/onboarding.py apps/api/app/api/v1/router.py apps/api/tests/unit/test_onboarding_routes.py
git commit -m "feat(api): flag de onboarding por tenant (migration 0012, GET/POST /onboarding)"
```

---

### Task 2: web — OnboardingGate no `/inicio` + `?aba=testes`

**Files:**
- Create: `apps/web/src/components/OnboardingGate.tsx`, `apps/web/__tests__/OnboardingGate.test.tsx`
- Modify: `apps/web/src/app/inicio/page.tsx`, `apps/web/src/app/conversas/page.tsx`, `apps/web/src/components/ConversationsPanel.tsx` (prop `initialOrigin`)
- Test: `apps/web/__tests__/ConversationsPanel.test.tsx` (teste novo da prop)

**Interfaces:**
- Consumes: `GET onboarding` → `{completed}` (Task 1) via `backendFetch`.
- Produces: `<OnboardingGate>{children}</OnboardingGate>`; `ConversationsPanel` com `initialOrigin?: "real" | "test"` (default `"real"`).

- [ ] **Step 1: Testes do gate (falhando)**

Criar `apps/web/__tests__/OnboardingGate.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OnboardingGate } from "@/components/OnboardingGate";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

const backendFetchMock = vi.mocked(backendFetch);

beforeEach(() => {
  backendFetchMock.mockReset();
  replaceMock.mockReset();
});

describe("OnboardingGate", () => {
  it("redireciona pra /boas-vindas quando o onboarding não foi completado", async () => {
    backendFetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ completed: false }),
    } as Response);

    render(
      <OnboardingGate>
        <p>conteudo do dashboard</p>
      </OnboardingGate>,
    );

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/boas-vindas"));
    expect(screen.queryByText("conteudo do dashboard")).not.toBeInTheDocument();
  });

  it("renderiza os children quando completado", async () => {
    backendFetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ completed: true }),
    } as Response);

    render(
      <OnboardingGate>
        <p>conteudo do dashboard</p>
      </OnboardingGate>,
    );

    await waitFor(() =>
      expect(screen.getByText("conteudo do dashboard")).toBeInTheDocument(),
    );
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("fail-open: erro de rede renderiza os children", async () => {
    backendFetchMock.mockRejectedValue(new Error("rede fora"));

    render(
      <OnboardingGate>
        <p>conteudo do dashboard</p>
      </OnboardingGate>,
    );

    await waitFor(() =>
      expect(screen.getByText("conteudo do dashboard")).toBeInTheDocument(),
    );
    expect(replaceMock).not.toHaveBeenCalled();
  });
});
```

E em `apps/web/__tests__/ConversationsPanel.test.tsx`, adicionar ao `describe`:

```tsx
  it("initialOrigin=test abre direto na aba Testes", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(<ConversationsPanel pollMs={0} initialOrigin="test" />);

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(([p]) => String(p).includes("origin=test")),
      ).toBe(true),
    );
    expect(screen.getByText("Nova conversa de teste")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/web && pnpm test -- OnboardingGate && pnpm test -- ConversationsPanel`
Expected: FAIL — componente não existe; prop desconhecida (type error/aba não abre).

- [ ] **Step 3: Implementar**

Criar `apps/web/src/components/OnboardingGate.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { backendFetch } from "@/lib/client-api";

/** Gate do tutorial de primeira abertura: tenant sem onboarding completado é
 * levado pro wizard /boas-vindas. Fail-open — erro na checagem nunca tranca o
 * painel (o tutorial é nice-to-have). */
export function OnboardingGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<"checking" | "allowed">("checking");

  useEffect(() => {
    let cancelled = false;
    async function check() {
      try {
        const response = await backendFetch("onboarding");
        if (response.ok) {
          const body = await response.json();
          if (!cancelled && body.completed === false) {
            router.replace("/boas-vindas");
            return;
          }
        }
      } catch {
        // fail-open
      }
      if (!cancelled) {
        setState("allowed");
      }
    }
    void check();
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (state === "checking") {
    return (
      <main className="flex flex-1 items-center justify-center bg-ground text-sm text-muted">
        Carregando...
      </main>
    );
  }
  return <>{children}</>;
}
```

`apps/web/src/app/inicio/page.tsx` — envolver o `DashboardPanel`:

```tsx
import { DashboardPanel } from "@/components/DashboardPanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { OnboardingGate } from "@/components/OnboardingGate";
import { TenantNav } from "@/components/TenantNav";

export default function InicioPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="inicio" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <main className="flex-1 overflow-y-auto bg-ground">
          <OnboardingGate>
            <DashboardPanel />
          </OnboardingGate>
        </main>
      </div>
    </div>
  );
}
```

`apps/web/src/components/ConversationsPanel.tsx` — assinatura e estado inicial:

```tsx
type Origin = "real" | "test";

export function ConversationsPanel({
  pollMs = 5000,
  initialOrigin = "real",
}: {
  pollMs?: number;
  initialOrigin?: Origin;
}) {
  const [origin, setOrigin] = useState<Origin>(initialOrigin);
```

(NÃO alterar mais nada no painel — só a assinatura e o `useState`.)

`apps/web/src/app/conversas/page.tsx` — ler o `searchParams` (Promise no Next 15):

```tsx
import { ConversationsPanel } from "@/components/ConversationsPanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default async function ConversasPage({
  searchParams,
}: {
  searchParams: Promise<{ aba?: string }>;
}) {
  const { aba } = await searchParams;
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="conversas" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <ConversationsPanel initialOrigin={aba === "testes" ? "test" : "real"} />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Rodar tudo + lint**

Run: `cd apps/web && pnpm test && pnpm lint`
Expected: PASS e lint sem erros novos.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/OnboardingGate.tsx apps/web/src/app/inicio/page.tsx apps/web/src/app/conversas/page.tsx apps/web/src/components/ConversationsPanel.tsx apps/web/__tests__/OnboardingGate.test.tsx apps/web/__tests__/ConversationsPanel.test.tsx
git commit -m "feat(web): OnboardingGate no /inicio e aba Testes via ?aba=testes"
```

---

### Task 3: web — wizard `/boas-vindas` + middleware + CLAUDE.md

**Files:**
- Create: `apps/web/src/app/boas-vindas/page.tsx`, `apps/web/src/components/OnboardingWizard.tsx`, `apps/web/__tests__/OnboardingWizard.test.tsx`
- Modify: `apps/web/src/middleware.ts` (matcher), `CLAUDE.md`

**Interfaces:**
- Consumes: `POST onboarding/complete` (Task 1), `GET whatsapp/webhook-config` → `{callback_url, verify_token}` (existe), `backendFetch`.

- [ ] **Step 1: Testes (falhando)**

Criar `apps/web/__tests__/OnboardingWizard.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OnboardingWizard } from "@/components/OnboardingWizard";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const backendFetchMock = vi.mocked(backendFetch);
const locationAssign = vi.fn();

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  backendFetchMock.mockReset();
  locationAssign.mockReset();
  Object.defineProperty(window, "location", {
    value: { assign: locationAssign },
    writable: true,
    configurable: true,
  });
  backendFetchMock.mockImplementation(async (path: string) => {
    if (String(path) === "whatsapp/webhook-config") {
      return jsonResponse({
        callback_url: "https://api.exemplo.com.br/api/v1/webhooks/whatsapp",
        verify_token: "meu-verify-token",
      });
    }
    return jsonResponse(null, 204);
  });
});

describe("OnboardingWizard", () => {
  it("navega do passo 1 ao 3 e conclui marcando completo", async () => {
    render(<OnboardingWizard />);

    expect(screen.getByText("Bem-vindo à Advoxs")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Começar" }));

    await waitFor(() =>
      expect(screen.getByText(/WhatsApp Business/)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Próximo" }));

    expect(screen.getByText(/Cobrança de clientes/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Concluir" }));

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(
          ([p, init]) => String(p) === "onboarding/complete" && init?.method === "POST",
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(locationAssign).toHaveBeenCalledWith("/inicio"));
  });

  it("mostra a callback URL e o verify token no passo do WhatsApp", async () => {
    render(<OnboardingWizard />);
    fireEvent.click(screen.getByRole("button", { name: "Começar" }));

    await waitFor(() =>
      expect(screen.getByLabelText("Callback URL")).toHaveValue(
        "https://api.exemplo.com.br/api/v1/webhooks/whatsapp",
      ),
    );
    expect(screen.getByLabelText("Verify token")).toHaveValue("meu-verify-token");
  });

  it("Configurar WhatsApp agora completa e navega pra config", async () => {
    render(<OnboardingWizard />);
    fireEvent.click(screen.getByRole("button", { name: "Começar" }));

    fireEvent.click(
      await screen.findByRole("button", { name: "Configurar WhatsApp agora" }),
    );

    await waitFor(() =>
      expect(locationAssign).toHaveBeenCalledWith("/configuracoes/whatsapp"),
    );
    expect(
      backendFetchMock.mock.calls.some(
        ([p, init]) => String(p) === "onboarding/complete" && init?.method === "POST",
      ),
    ).toBe(true);
  });

  it("Pular e testar os agentes está em todos os passos e navega pra aba Testes", async () => {
    render(<OnboardingWizard />);

    expect(screen.getByText("Pular e testar os agentes")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Pular e testar os agentes"));

    await waitFor(() =>
      expect(locationAssign).toHaveBeenCalledWith("/conversas?aba=testes"),
    );
  });

  it("POST falhando não impede a navegação", async () => {
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        throw new Error("rede fora");
      }
      return jsonResponse(null);
    });

    render(<OnboardingWizard />);
    fireEvent.click(screen.getByText("Pular e testar os agentes"));

    await waitFor(() =>
      expect(locationAssign).toHaveBeenCalledWith("/conversas?aba=testes"),
    );
  });
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/web && pnpm test -- OnboardingWizard`
Expected: FAIL — componente não existe.

- [ ] **Step 3: Implementar o wizard**

Criar `apps/web/src/components/OnboardingWizard.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";

type WebhookConfig = { callback_url: string; verify_token: string };

async function completeAndGo(href: string) {
  try {
    await backendFetch("onboarding/complete", { method: "POST" });
  } catch {
    // Best-effort: pior caso o wizard reaparece no próximo login.
  }
  window.location.assign(href);
}

export function OnboardingWizard() {
  const [step, setStep] = useState(1);
  const [webhookConfig, setWebhookConfig] = useState<WebhookConfig | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    async function loadConfig() {
      try {
        const response = await backendFetch("whatsapp/webhook-config");
        if (response.ok) {
          const config = await response.json().catch(() => null);
          if (config?.callback_url && config?.verify_token) {
            setWebhookConfig(config);
          }
        }
      } catch {
        // sem config, o passo 2 fica só com o texto
      }
    }
    void loadConfig();
  }, []);

  async function handleCopy(field: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(field);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      // clipboard indisponível — sem feedback, sem quebrar
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-ground px-6 py-10">
      <div className="w-full max-w-2xl">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted">
          Configurações iniciais · passo {step} de 3
        </p>

        {step === 1 && (
          <section className="mt-4">
            <h1 className="font-display text-3xl font-semibold text-ink">
              Bem-vindo à Advoxs
            </h1>
            <p className="mt-4 text-sm leading-relaxed text-ink">
              Seu escritório agora tem agentes de IA prontos pra atender clientes pelo
              WhatsApp: uma secretária faz a triagem e especialistas respondem dúvidas
              jurídicas, consultando a base de conhecimento que você subir. O consumo é
              pago em créditos — o pacote que você comprou já está na sua conta.
            </p>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              Vamos passar pelas duas configurações principais. Você pode fazer agora ou
              depois — tudo fica em Configurações.
            </p>
            <div className="mt-6">
              <button
                type="button"
                onClick={() => setStep(2)}
                className="rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink"
              >
                Começar
              </button>
            </div>
          </section>
        )}

        {step === 2 && (
          <section className="mt-4">
            <h1 className="font-display text-3xl font-semibold text-ink">
              Conectar o WhatsApp Business
            </h1>
            <p className="mt-4 text-sm leading-relaxed text-ink">
              É por ele que os agentes atendem seus clientes. O setup é feito no painel da
              Meta (developers.facebook.com) e depois colado aqui na plataforma:
            </p>
            <ol className="mt-3 flex list-decimal flex-col gap-2 pl-5 text-sm text-ink">
              <li>Crie (ou acesse) um app na Meta e adicione um System User com role Admin.</li>
              <li>
                Gere um token de acesso permanente com as permissões
                <code className="mx-1 rounded bg-surface px-1">whatsapp_business_management</code>
                e
                <code className="mx-1 rounded bg-surface px-1">whatsapp_business_messaging</code>.
              </li>
              <li>Adicione e verifique o número do escritório (você vai precisar do PIN de 2 fatores).</li>
              <li>
                Configure o webhook do app com os valores abaixo e assine o campo{" "}
                <code className="rounded bg-surface px-1">messages</code>:
              </li>
            </ol>
            {webhookConfig && (
              <div className="mt-3 flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <input
                    readOnly
                    aria-label="Callback URL"
                    value={webhookConfig.callback_url}
                    className="flex-1 rounded border border-line bg-surface px-3 py-2 font-mono text-xs text-ink"
                  />
                  <button
                    type="button"
                    aria-label="Copiar Callback URL"
                    onClick={() => void handleCopy("url", webhookConfig.callback_url)}
                    className="rounded border border-line px-3 py-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                  >
                    {copied === "url" ? "Copiado!" : "Copiar"}
                  </button>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    readOnly
                    aria-label="Verify token"
                    value={webhookConfig.verify_token}
                    className="flex-1 rounded border border-line bg-surface px-3 py-2 font-mono text-xs text-ink"
                  />
                  <button
                    type="button"
                    aria-label="Copiar Verify token"
                    onClick={() => void handleCopy("token", webhookConfig.verify_token)}
                    className="rounded border border-line px-3 py-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                  >
                    {copied === "token" ? "Copiado!" : "Copiar"}
                  </button>
                </div>
              </div>
            )}
            <p className="mt-3 text-sm leading-relaxed text-muted">
              Com tudo pronto na Meta, cole as credenciais na página de configuração — a
              plataforma valida, registra o número e ativa o recebimento automaticamente.
            </p>
            <div className="mt-6 flex items-center gap-4">
              <button
                type="button"
                onClick={() => void completeAndGo("/configuracoes/whatsapp")}
                className="rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink"
              >
                Configurar WhatsApp agora
              </button>
              <button
                type="button"
                onClick={() => setStep(3)}
                className="rounded-sm border border-line px-4 py-2.5 text-sm font-medium text-ink transition-colors hover:border-accent"
              >
                Próximo
              </button>
            </div>
          </section>
        )}

        {step === 3 && (
          <section className="mt-4">
            <h1 className="font-display text-3xl font-semibold text-ink">
              Cobrança de clientes (opcional)
            </h1>
            <p className="mt-4 text-sm leading-relaxed text-ink">
              Se quiser, o escritório pode cobrar os próprios clientes pelo atendimento dos
              agentes: eles compram créditos seus, pagos direto na SUA conta Stripe — a
              plataforma nunca toca nesse dinheiro.
            </p>
            <ol className="mt-3 flex list-decimal flex-col gap-2 pl-5 text-sm text-ink">
              <li>Cole a secret key e o webhook secret da sua conta Stripe.</li>
              <li>Defina a conversão de tokens por crédito e cadastre seus pacotes.</li>
              <li>
                Aponte um webhook da sua Stripe pra URL exibida na página (evento{" "}
                <code className="rounded bg-surface px-1">checkout.session.completed</code>).
              </li>
            </ol>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              Sem configurar, os agentes atendem seus clientes normalmente, sem cobrança.
            </p>
            <div className="mt-6 flex items-center gap-4">
              <button
                type="button"
                onClick={() => void completeAndGo("/configuracoes/cobranca-clientes")}
                className="rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink"
              >
                Configurar cobrança
              </button>
              <button
                type="button"
                onClick={() => void completeAndGo("/inicio")}
                className="rounded-sm border border-line px-4 py-2.5 text-sm font-medium text-ink transition-colors hover:border-accent"
              >
                Concluir
              </button>
            </div>
          </section>
        )}

        <footer className="mt-10 border-t border-line pt-4">
          <button
            type="button"
            onClick={() => void completeAndGo("/conversas?aba=testes")}
            className="text-sm text-muted underline transition-colors hover:text-ink"
          >
            Pular e testar os agentes
          </button>
        </footer>
      </div>
    </main>
  );
}
```

Criar `apps/web/src/app/boas-vindas/page.tsx`:

```tsx
import { OnboardingWizard } from "@/components/OnboardingWizard";

export default function BoasVindasPage() {
  return <OnboardingWizard />;
}
```

- [ ] **Step 4: Middleware**

Em `apps/web/src/middleware.ts`, no `config.matcher`, adicionar a linha (junto das rotas de tenant, após `"/inicio/:path*"`):

```ts
    "/boas-vindas/:path*",
```

- [ ] **Step 5: Rodar tudo + lint**

Run: `cd apps/web && pnpm test && pnpm lint`
Expected: PASS e lint sem erros novos.

- [ ] **Step 6: CLAUDE.md**

Duas edições verbatim:

1. Localizar (seção Frontend, bullet `/perfil` — final):

```
A logo, quando cadastrada, substitui o monograma na nav lateral.
```

Substituir por:

```
A logo, quando cadastrada, substitui o monograma na nav lateral.
- **`/boas-vindas`** — ✅ implementada: tutorial de primeira abertura (configurações iniciais). Wizard de 3 passos (boas-vindas → WhatsApp Business com callback URL/verify token copiáveis via `GET /whatsapp/webhook-config` → cobrança de clientes), mostrado uma única vez por tenant: `tenants.onboarding_completed_at` (migration `0012`, com backfill — tenants existentes nunca veem), `GET /api/v1/onboarding` + `POST /api/v1/onboarding/complete` (idempotente; disparado em qualquer saída do wizard — Concluir, "configurar agora" ou "Pular e testar os agentes", que leva pra `/conversas?aba=testes`). O gate fica no `/inicio` (`OnboardingGate`, client, fail-open — erro na checagem nunca tranca o painel).
```

2. Localizar (bullet do api no "Estado atual do repositório"):

```
(aba Testes do painel, ver seção Frontend/`/conversas`) — e a **cobrança do cliente final** —
```

Substituir por:

```
(aba Testes do painel, ver seção Frontend/`/conversas`) — o **onboarding de primeira abertura** — `GET /api/v1/onboarding` e `POST /api/v1/onboarding/complete` (ver seção Frontend/`/boas-vindas`) — e a **cobrança do cliente final** —
```

Se algum trecho não bater verbatim, PARAR e reportar.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/app/boas-vindas/page.tsx apps/web/src/components/OnboardingWizard.tsx apps/web/__tests__/OnboardingWizard.test.tsx apps/web/src/middleware.ts CLAUDE.md
git commit -m "feat(web): wizard de boas-vindas em /boas-vindas (tutorial de primeira abertura)"
```

---

## Nota pós-deploy (manual, fora do código)

Migration `0012` roda automaticamente no pipeline (com o backfill — contas existentes não veem o tutorial). Nenhuma env nova.
