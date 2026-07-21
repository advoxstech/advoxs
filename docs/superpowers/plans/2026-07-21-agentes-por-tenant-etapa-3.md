# Agentes por Tenant — Etapa 3 (frontend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar ao escritório uma interface no painel (`apps/web`) para gerenciar os próprios agentes de IA (`/agentes` — listar/criar/apagar; `/agentes/[id]` — editar e anexar/desanexar base de conhecimento) e escolher explicitamente o agente de destino ao subir um arquivo em `/base-de-conhecimento` — completando a feature de agentes por tenant iniciada nas Etapas 1 (modelo de dados + CRUD no `api`) e 2 (motor dinâmico no `agents`), ambas já em produção.

**Architecture:** Duas páginas novas em `apps/web` (`/agentes`, `/agentes/[id]`) seguindo exatamente o padrão já estabelecido em `/configuracoes/cobranca-clientes` (painel client-side com `useState`/`backendFetch`, sem Server Actions) e em `/admin/tenants/[id]` (rota dinâmica com `params: Promise<{ id: string }>`). Antes do frontend, uma pequena adição no `apps/api` fecha um gap real: hoje não existe nenhum endpoint que devolva a lista de arquivos anexados a um agente específico — só o vínculo em si (`POST`/`DELETE`) — e o attach duplicado do mesmo arquivo no mesmo agente quebra com `500` em vez de `409` (bug conhecido, documentado como pendência da Etapa 1, nunca exercitado de verdade porque não havia UI).

**Tech Stack:** Next.js 15 (App Router, Vitest + Testing Library) para o frontend; FastAPI + SQLAlchemy async para a adição pequena no backend — mesmas stacks já em uso, sem dependências novas.

## Global Constraints

- `POST /api/v1/knowledge-base/files` já aceita `agent_id` como campo **opcional** (com fallback pro ponto de entrada do tenant) desde a Etapa 1 — isso não muda. A obrigatoriedade de escolher um agente é uma decisão só de UX: o frontend torna o `<select>` obrigatório (o usuário sempre escolhe), mas o backend continua aceitando omissão (nenhuma mudança de contrato necessária).
- Toda rota nova tenant-scoped precisa de 2 coisas, ou fica inacessível/exposta incorretamente — **histórico real de bugs de produção neste projeto por esquecer uma delas**:
  1. Prefixo na allowlist do proxy (`apps/web/src/lib/backend.ts`, `ALLOWED_PREFIXES`) — sem isso, toda chamada do front pro backend devolve `404` mesmo com a rota do `api` funcionando.
  2. Entrada no matcher do middleware (`apps/web/src/middleware.ts`, `config.matcher`) — sem isso, a rota fica acessível **sem sessão** (bug real já ocorrido com `/creditos`, corrigido no commit `4c41deb`).
- Sem Server Actions nos painéis pós-login (só em login/signup/logout) — todo CRUD é client-side via `backendFetch` + `useState`, seguindo `EndCustomerBillingPanel.tsx`/`KnowledgeBasePanel.tsx`.
- Confirmação de exclusão é sempre `window.confirm(...)` — não existe (e não se deve introduzir) um componente de modal de confirmação neste projeto.
- Erros do backend são extraídos do corpo via `body?.detail` (ou o helper `extractErrorDetail`), nunca assumindo que toda resposta de erro tem corpo JSON (`.json().catch(() => null)`).
- Nenhuma mudança na tabela `agents`/`agent_knowledge_base_files` nem no motor do `apps/agents` — esta etapa é só frontend + 1 endpoint de leitura + 1 fix de bug no `apps/api`.

---

### Task 1: `apps/api` — lista de arquivos de um agente + fix do attach duplicado

**Files:**
- Modify: `apps/api/app/api/v1/agents.py`
- Test: `apps/api/tests/unit/test_agents_routes.py`

**Interfaces:**
- Consumes: nada de outra task deste plano (task independente, primeira da sequência).
- Produces: `GET /api/v1/agents/{agent_id}/knowledge-base-files -> list[KnowledgeBaseFileOut]` (mesmo schema já usado por `GET /api/v1/knowledge-base/files`) — consumido pela Task 4 (`AgentDetail`). `POST /api/v1/agents/{agent_id}/knowledge-base-files` passa a devolver `409` (em vez de `500`) quando o arquivo já está anexado àquele agente.

- [ ] **Step 1: Escrever os testes que falham**

Em `apps/api/tests/unit/test_agents_routes.py`, adicionar o import de `IntegrityError` no topo do arquivo (junto aos imports existentes):

```python
from sqlalchemy.exc import IntegrityError
```

Adicionar, dentro de `class TestAttachKnowledgeBaseFile` (depois de `test_arquivo_de_outro_tenant_retorna_404`), o teste do fix de duplicado:

```python
    def test_arquivo_ja_anexado_retorna_409(self, client, session) -> None:
        session.scalar.side_effect = [_agent(), SimpleNamespace(id=uuid.uuid4())]
        session.commit.side_effect = IntegrityError("stmt", {}, Exception("dup"))

        response = client.post(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files",
            json={"knowledge_base_file_id": str(uuid.uuid4())},
        )

        assert response.status_code == 409
        session.rollback.assert_awaited_once()
```

Adicionar, no fim do arquivo, uma nova classe de testes para a listagem:

```python
class TestListKnowledgeBaseFiles:
    def test_lista_arquivos_anexados(self, client, session) -> None:
        session.scalar.return_value = _agent()
        file_row = SimpleNamespace(
            id=uuid.uuid4(),
            filename="regimento.pdf",
            size_bytes=1024,
            mime_type="application/pdf",
            status="ready",
            error_message=None,
            uploaded_at=datetime.now(UTC),
        )
        session.execute.return_value = _execute_returning([file_row])

        response = client.get(f"/api/v1/agents/{AGENT_ID}/knowledge-base-files")

        assert response.status_code == 200
        assert response.json()[0]["filename"] == "regimento.pdf"

    def test_agente_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.get(f"/api/v1/agents/{AGENT_ID}/knowledge-base-files")

        assert response.status_code == 404
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_routes.py -v`
Expected: FAIL em `test_arquivo_ja_anexado_retorna_409` (o commit real, sem `try/except`, deixa o `IntegrityError` subir sem tratamento — a resposta HTTP vira `500` genérico do FastAPI, não `409`) e em ambos os testes de `TestListKnowledgeBaseFiles` (`404 Not Found` — a rota `GET /{agent_id}/knowledge-base-files` não existe ainda).

- [ ] **Step 3: Implementar**

Em `apps/api/app/api/v1/agents.py`, adicionar o import de `IntegrityError` e de `KnowledgeBaseFileOut` no topo do arquivo:

```python
from sqlalchemy.exc import IntegrityError
```

```python
from app.schemas.knowledge_base import KnowledgeBaseFileOut
```

(mantendo os imports existentes — `from app.models import Agent, AgentKnowledgeBaseFile, KnowledgeBaseFile` já traz `KnowledgeBaseFile`, que a query nova usa.)

Substituir o corpo de `attach_knowledge_base_file` (função já existente) — trocar só as 3 últimas linhas:

```python
    link = AgentKnowledgeBaseFile(agent_id=agent_id, knowledge_base_file_id=file.id)
    session.add(link)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este arquivo já está anexado a este agente",
        )
    return AgentKnowledgeBaseFileOut.model_validate(link)
```

Adicionar, no fim do arquivo (depois de `detach_knowledge_base_file`), a rota nova:

```python
@router.get("/{agent_id}/knowledge-base-files")
async def list_agent_knowledge_base_files(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[KnowledgeBaseFileOut]:
    await _get_agent(agent_id, ctx, session)

    result = await session.execute(
        select(KnowledgeBaseFile)
        .join(
            AgentKnowledgeBaseFile,
            AgentKnowledgeBaseFile.knowledge_base_file_id == KnowledgeBaseFile.id,
        )
        .where(AgentKnowledgeBaseFile.agent_id == agent_id)
        .order_by(KnowledgeBaseFile.uploaded_at.desc())
    )
    return [KnowledgeBaseFileOut.model_validate(f) for f in result.scalars().all()]
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_routes.py tests/unit/test_knowledge_base_routes.py -v`
Expected: todos passam — a rota nova não conflita com nenhuma rota existente (métodos/paths distintos), e o fix do `409` não muda nenhum teste já passando (`test_anexa_arquivo_existente` continua fazendo commit sem erro, então nunca entra no `except`).

- [ ] **Step 5: Rodar a suíte completa**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v`
Expected: todos passam ou skip (mesmos skips pré-existentes de integração que exigem Postgres real).

- [ ] **Step 6: Lint**

Run: `cd apps/api && uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/api/v1/agents.py apps/api/tests/unit/test_agents_routes.py
git commit -m "feat(api): lista arquivos anexados a um agente e corrige attach duplicado (409)"
```

---

### Task 2: `apps/web` — libera o proxy e o middleware pra `/agentes`, tipo `Agent`

**Files:**
- Modify: `apps/web/src/lib/backend.ts`
- Modify: `apps/web/src/middleware.ts`
- Modify: `apps/web/src/lib/types.ts`
- Test: `apps/web/__tests__/backend.test.ts`

**Interfaces:**
- Consumes: nada de outra task deste plano.
- Produces: `Agent` (interface TypeScript, em `apps/web/src/lib/types.ts`) — `{ id: string; name: string; instructions: string; is_entry_point: boolean; created_at: string; updated_at: string }`, espelhando `AgentOut` do `apps/api` (`apps/api/app/schemas/agents.py`) — consumido pelas Tasks 3, 4 e 5. Prefixo `"agents"` liberado no proxy — consumido por toda chamada `backendFetch("agents...")` das Tasks 3 e 4.

- [ ] **Step 1: Escrever o teste que falha**

Em `apps/web/__tests__/backend.test.ts`, adicionar (depois do teste `"permite rotas de conversas de teste e onboarding"`):

```ts
  it("permite rotas de agentes", () => {
    expect(isAllowedPath(["agents"])).toBe(true);
    expect(isAllowedPath(["agents", "abc123", "knowledge-base-files"])).toBe(true);
  });
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `cd apps/web && pnpm test -- backend.test.ts`
Expected: FAIL — `"agents"` ainda não está em `ALLOWED_PREFIXES`, então `isAllowedPath(["agents"])` retorna `false`.

- [ ] **Step 3: Implementar**

Em `apps/web/src/lib/backend.ts`, adicionar `"agents"` ao array `ALLOWED_PREFIXES`:

```ts
const ALLOWED_PREFIXES = [
  "conversations",
  "test-conversations",
  "knowledge-base",
  "whatsapp",
  "signup",
  "billing",
  "dashboard",
  "profile",
  "end-customer-billing",
  "onboarding",
  "agents",
];
```

Em `apps/web/src/middleware.ts`, adicionar `"/agentes/:path*",` ao `config.matcher` (depois de `"/base-de-conhecimento/:path*",`):

```ts
export const config = {
  matcher: [
    "/",
    "/login",
    "/inicio/:path*",
    "/boas-vindas/:path*",
    "/conversas/:path*",
    "/base-de-conhecimento/:path*",
    "/agentes/:path*",
    "/configuracoes/:path*",
    "/creditos/:path*",
    "/perfil/:path*",
    "/admin/:path*",
  ],
};
```

Em `apps/web/src/lib/types.ts`, adicionar a interface `Agent` (depois de `Profile`):

```ts
export interface Agent {
  id: string;
  name: string;
  instructions: string;
  is_entry_point: boolean;
  created_at: string;
  updated_at: string;
}
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/web && pnpm test -- backend.test.ts`
Expected: todos os testes do arquivo passam.

- [ ] **Step 5: Rodar a suíte completa + build**

Run: `cd apps/web && pnpm test && pnpm lint && pnpm build`
Expected: tudo verde — nenhum outro arquivo usa `Agent` ainda, então o `tsc` não tem nada a checar contra o tipo novo além dele mesmo estar bem formado.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/backend.ts apps/web/src/middleware.ts apps/web/src/lib/types.ts apps/web/__tests__/backend.test.ts
git commit -m "feat(web): libera o proxy e protege /agentes no middleware, tipo Agent"
```

---

### Task 3: `apps/web` — página `/agentes` (listar, criar, apagar) + item na navegação

**Files:**
- Create: `apps/web/src/app/agentes/page.tsx`
- Create: `apps/web/src/components/AgentsPanel.tsx`
- Create: `apps/web/__tests__/AgentsPanel.test.tsx`
- Modify: `apps/web/src/components/TenantNav.tsx`
- Modify: `apps/web/__tests__/TenantNav.test.tsx`

**Interfaces:**
- Consumes: `Agent` (Task 2), `GET /api/v1/agents`, `POST /api/v1/agents`, `DELETE /api/v1/agents/{id}` (já existentes desde a Etapa 1).
- Produces: `AgentsPanel` (componente client) e a rota `/agentes` — consumida pela Task 4 (link "← Agentes" de volta) e pela Task 5 (nenhuma dependência direta, mas ambas compartilham o item `"agentes"` de `TenantNavItem`).

- [ ] **Step 1: Escrever o teste que falha**

Criar `apps/web/__tests__/AgentsPanel.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { AgentsPanel } from "@/components/AgentsPanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const AGENTS = [
  {
    id: "a1",
    name: "Secretária",
    instructions: "x",
    is_entry_point: true,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
  },
  {
    id: "a2",
    name: "Condominial",
    instructions: "y",
    is_entry_point: false,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
  },
];

describe("AgentsPanel", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
  });

  it("lista os agentes com badge de ponto de entrada", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => AGENTS });

    render(<AgentsPanel />);

    await waitFor(() => expect(screen.getByText("Secretária")).toBeInTheDocument());
    expect(screen.getByText("Condominial")).toBeInTheDocument();
    expect(screen.getByText("ponto de entrada")).toBeInTheDocument();
  });

  it("cria um agente novo e recarrega a lista", async () => {
    const created = {
      id: "a3",
      name: "Novo",
      instructions: "z",
      is_entry_point: false,
      created_at: "2026-07-20T00:00:00Z",
      updated_at: "2026-07-20T00:00:00Z",
    };
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") return { ok: true, json: async () => created };
      return { ok: true, json: async () => [...AGENTS, created] };
    });

    render(<AgentsPanel />);
    await waitFor(() => expect(screen.getByText("Secretária")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Nome"), { target: { value: "Novo" } });
    fireEvent.change(screen.getByLabelText("Instruções"), { target: { value: "z" } });
    fireEvent.click(screen.getByRole("button", { name: "Criar agente" }));

    await waitFor(() => expect(screen.getByText("Novo")).toBeInTheDocument());
  });

  it("exclui um agente após confirmação", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "DELETE") return { ok: true, json: async () => null };
      return { ok: true, json: async () => AGENTS };
    });

    render(<AgentsPanel />);
    await waitFor(() => expect(screen.getByText("Secretária")).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole("button", { name: "Excluir" })[0]);

    await waitFor(() => expect(screen.queryByText("Secretária")).not.toBeInTheDocument());
    confirmSpy.mockRestore();
  });

  it("mostra erro do backend ao tentar apagar o ponto de entrada", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "DELETE") {
        return {
          ok: false,
          json: async () => ({
            detail:
              "Não é possível apagar o agente ponto de entrada — marque outro agente como ponto de entrada antes",
          }),
        };
      }
      return { ok: true, json: async () => AGENTS };
    });

    render(<AgentsPanel />);
    await waitFor(() => expect(screen.getByText("Secretária")).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole("button", { name: "Excluir" })[0]);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/ponto de entrada/));
  });
});
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `cd apps/web && pnpm test -- AgentsPanel.test.tsx`
Expected: FAIL — `Cannot find module '@/components/AgentsPanel'` (o componente ainda não existe).

- [ ] **Step 3: Implementar `AgentsPanel`**

Criar `apps/web/src/components/AgentsPanel.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { backendFetch } from "@/lib/client-api";
import type { Agent } from "@/lib/types";

const EMPTY_FORM = { name: "", instructions: "", is_entry_point: false };

function extractErrorDetail(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export function AgentsPanel() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [creating, setCreating] = useState(false);

  async function load() {
    try {
      const response = await backendFetch("agents");
      if (response.ok) {
        setAgents(await response.json());
      }
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setCreating(true);
    try {
      const response = await backendFetch("agents", {
        method: "POST",
        body: JSON.stringify(form),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(body, "Falha ao criar agente — tente novamente."));
        return;
      }
      await load();
      setForm(EMPTY_FORM);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(agent: Agent) {
    if (!window.confirm(`Excluir o agente "${agent.name}"?`)) return;
    try {
      const response = await backendFetch(`agents/${agent.id}`, { method: "DELETE" });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(extractErrorDetail(body, "Falha ao excluir — tente novamente."));
        return;
      }
      setAgents(agents.filter((a) => a.id !== agent.id));
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    }
  }

  if (!loaded) {
    return (
      <main className="flex flex-1 items-center justify-center bg-ground text-sm text-muted">
        Carregando...
      </main>
    );
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      <header className="border-b border-line px-8 py-5">
        <h1 className="font-display text-xl font-semibold text-ink">Agentes</h1>
        <p className="text-sm text-muted">
          Cada agente responde por conta própria, com suas instruções e sua base de conhecimento.
        </p>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <ul className="max-w-md">
          {agents.length === 0 && (
            <li className="py-4 text-sm text-muted">Nenhum agente cadastrado ainda.</li>
          )}
          {agents.map((agent) => (
            <li
              key={agent.id}
              className="flex items-center justify-between border-b border-line py-3"
            >
              <div className="min-w-0 flex-1">
                <Link href={`/agentes/${agent.id}`} className="font-medium text-ink hover:underline">
                  {agent.name}
                </Link>
                {agent.is_entry_point && (
                  <span className="ml-2 rounded-full bg-accent-soft px-3 py-1 font-mono text-[10px] uppercase tracking-[0.15em] text-accent">
                    ponto de entrada
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={() => void handleDelete(agent)}
                className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
              >
                Excluir
              </button>
            </li>
          ))}
        </ul>

        <hr className="my-6 border-line" />

        <h2 className="font-display text-lg font-semibold text-ink">Criar agente</h2>
        <form onSubmit={handleCreate} className="mt-4 flex max-w-md flex-col gap-4">
          <label className="flex flex-col gap-1 text-sm text-ink">
            Nome
            <input
              required
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-ink">
            Instruções
            <textarea
              required
              rows={6}
              value={form.instructions}
              onChange={(event) => setForm({ ...form, instructions: event.target.value })}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={form.is_entry_point}
              onChange={(event) => setForm({ ...form, is_entry_point: event.target.checked })}
            />
            Ponto de entrada (recebe a primeira mensagem de conversas novas)
          </label>
          <button
            type="submit"
            disabled={creating}
            className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
          >
            {creating ? "Criando..." : "Criar agente"}
          </button>
        </form>
      </div>
    </main>
  );
}
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/web && pnpm test -- AgentsPanel.test.tsx`
Expected: os 4 testes passam.

- [ ] **Step 5: Criar a página**

Criar `apps/web/src/app/agentes/page.tsx`:

```tsx
import { AgentsPanel } from "@/components/AgentsPanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default function AgentesPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="agentes" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <AgentsPanel />
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Escrever os testes que falham para o item de navegação**

Em `apps/web/__tests__/TenantNav.test.tsx`, adicionar a asserção do item novo dentro do teste `"renderiza o item ativo como texto (não link) e os demais como links"` (logo depois da asserção de `"Base"`):

```tsx
    expect(screen.getByText("Agentes").closest("a")).toHaveAttribute("href", "/agentes");
```

E adicionar um teste dedicado (depois de `"marca inicio como ativo quando active='inicio'"`):

```tsx
  it("marca agentes como ativo quando active='agentes'", () => {
    render(<TenantNav active="agentes" />);

    expect(screen.getByText("Agentes").closest("a")).toBeNull();
    expect(screen.getByText("Conversas").closest("a")).toHaveAttribute("href", "/conversas");
  });
```

- [ ] **Step 7: Rodar e confirmar falha**

Run: `cd apps/web && pnpm test -- TenantNav.test.tsx`
Expected: FAIL — `screen.getByText("Agentes")` não encontra nada (o item ainda não existe no array `ITEMS`), e `active="agentes"` não é um valor válido de `TenantNavItem` ainda (erro de tipo do TypeScript, mas o teste em si falha em runtime por não achar o texto).

- [ ] **Step 8: Implementar o item de navegação**

Em `apps/web/src/components/TenantNav.tsx`, atualizar a union `TenantNavItem`:

```ts
type TenantNavItem =
  | "inicio"
  | "conversas"
  | "base"
  | "agentes"
  | "config"
  | "cobranca"
  | "creditos"
  | "perfil";
```

E adicionar, no array `ITEMS`, um novo item logo depois do item `"base"` (antes do item `"config"`):

```ts
  {
    key: "agentes",
    href: "/agentes",
    label: "Agentes",
    icon: (
      <>
        <rect x="4" y="8" width="16" height="12" rx="2" />
        <path d="M12 8V4M9 13h.01M15 13h.01" />
      </>
    ),
  },
```

- [ ] **Step 9: Rodar e confirmar sucesso**

Run: `cd apps/web && pnpm test -- TenantNav.test.tsx`
Expected: todos os testes do arquivo passam.

- [ ] **Step 10: Rodar a suíte completa + build**

Run: `cd apps/web && pnpm test && pnpm lint && pnpm build`
Expected: tudo verde.

- [ ] **Step 11: Commit**

```bash
git add apps/web/src/app/agentes/page.tsx apps/web/src/components/AgentsPanel.tsx apps/web/__tests__/AgentsPanel.test.tsx apps/web/src/components/TenantNav.tsx apps/web/__tests__/TenantNav.test.tsx
git commit -m "feat(web): página /agentes (listar, criar, apagar) e item na navegação"
```

---

### Task 4: `apps/web` — página `/agentes/[id]` (editar + base de conhecimento)

**Files:**
- Create: `apps/web/src/app/agentes/[id]/page.tsx`
- Create: `apps/web/src/components/AgentDetail.tsx`
- Create: `apps/web/__tests__/AgentDetail.test.tsx`

**Interfaces:**
- Consumes: `Agent` (Task 2), `GET /api/v1/agents` (reaproveitado — não existe `GET /agents/{id}` avulso, então resolve o agente filtrando a lista completa pelo `id`), `GET /api/v1/agents/{id}/knowledge-base-files` (Task 1), `GET /api/v1/knowledge-base/files` (já existente), `PATCH /api/v1/agents/{id}`, `POST`/`DELETE /api/v1/agents/{id}/knowledge-base-files[/{file_id}]` (já existentes desde a Etapa 1).
- Produces: `AgentDetail` (componente client, prop `agentId: string`) e a rota `/agentes/[id]` — consumida pela Task 3 (link de cada agente na lista) e pela Task 5 (link `/base-de-conhecimento?agent_id=...` apontado a partir daqui).

- [ ] **Step 1: Escrever o teste que falha**

Criar `apps/web/__tests__/AgentDetail.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { AgentDetail } from "@/components/AgentDetail";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const AGENT = {
  id: "a1",
  name: "Secretária",
  instructions: "Você é a secretária.",
  is_entry_point: true,
  created_at: "2026-07-20T00:00:00Z",
  updated_at: "2026-07-20T00:00:00Z",
};

function mockLoad(overrides?: { attached?: unknown[]; all?: unknown[] }) {
  mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (!init && path === "agents") return { ok: true, json: async () => [AGENT] };
    if (!init && path === "agents/a1/knowledge-base-files") {
      return { ok: true, json: async () => overrides?.attached ?? [] };
    }
    if (!init && path === "knowledge-base/files") {
      return { ok: true, json: async () => overrides?.all ?? [] };
    }
    return { ok: true, json: async () => null };
  });
}

describe("AgentDetail", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
  });

  it("carrega e preenche o formulário com os dados do agente", async () => {
    mockLoad();

    render(<AgentDetail agentId="a1" />);

    await waitFor(() => expect(screen.getByDisplayValue("Secretária")).toBeInTheDocument());
    expect(screen.getByDisplayValue("Você é a secretária.")).toBeInTheDocument();
  });

  it("mostra 'agente não encontrado' quando o id não existe na lista", async () => {
    mockedFetch.mockImplementation(async (path: string) => {
      if (path === "agents") return { ok: true, json: async () => [AGENT] };
      return { ok: true, json: async () => [] };
    });

    render(<AgentDetail agentId="inexistente" />);

    await waitFor(() => expect(screen.getByText("Agente não encontrado.")).toBeInTheDocument());
  });

  it("lista os arquivos anexados e omite eles do seletor de anexar", async () => {
    mockLoad({
      attached: [{ id: "f1", filename: "regimento.pdf", status: "ready" }],
      all: [
        { id: "f1", filename: "regimento.pdf", status: "ready" },
        { id: "f2", filename: "modelo.docx", status: "ready" },
      ],
    });

    render(<AgentDetail agentId="a1" />);

    await waitFor(() => expect(screen.getByText("regimento.pdf")).toBeInTheDocument());
    expect(screen.getByRole("option", { name: "modelo.docx" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "regimento.pdf" })).not.toBeInTheDocument();
  });

  it("desanexa um arquivo após confirmação", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "DELETE") return { ok: true, json: async () => null };
      if (path === "agents") return { ok: true, json: async () => [AGENT] };
      if (path === "agents/a1/knowledge-base-files") {
        return {
          ok: true,
          json: async () => [{ id: "f1", filename: "regimento.pdf", status: "ready" }],
        };
      }
      return { ok: true, json: async () => [] };
    });

    render(<AgentDetail agentId="a1" />);
    await waitFor(() => expect(screen.getByText("regimento.pdf")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Desanexar" }));

    await waitFor(() => expect(screen.queryByText("regimento.pdf")).not.toBeInTheDocument());
    confirmSpy.mockRestore();
  });

  it("salva as alterações do formulário", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        return { ok: true, json: async () => ({ ...AGENT, name: "Nova Secretária" }) };
      }
      if (!init && path === "agents") return { ok: true, json: async () => [AGENT] };
      return { ok: true, json: async () => [] };
    });

    render(<AgentDetail agentId="a1" />);
    await waitFor(() => expect(screen.getByDisplayValue("Secretária")).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue("Secretária"), {
      target: { value: "Nova Secretária" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Salvar" }));

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith(
        "agents/a1",
        expect.objectContaining({ method: "PATCH" }),
      ),
    );
  });
});
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `cd apps/web && pnpm test -- AgentDetail.test.tsx`
Expected: FAIL — `Cannot find module '@/components/AgentDetail'`.

- [ ] **Step 3: Implementar `AgentDetail`**

Criar `apps/web/src/components/AgentDetail.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { backendFetch } from "@/lib/client-api";
import type { Agent } from "@/lib/types";

type AttachedFile = {
  id: string;
  filename: string;
  status: "processing" | "ready" | "error";
};

function extractErrorDetail(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export function AgentDetail({ agentId }: { agentId: string }) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const [allFiles, setAllFiles] = useState<AttachedFile[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [isEntryPoint, setIsEntryPoint] = useState(false);
  const [saving, setSaving] = useState(false);
  const [selectedFileId, setSelectedFileId] = useState("");
  const [attaching, setAttaching] = useState(false);

  async function load() {
    try {
      const [agentsResponse, attachedResponse, allFilesResponse] = await Promise.all([
        backendFetch("agents"),
        backendFetch(`agents/${agentId}/knowledge-base-files`),
        backendFetch("knowledge-base/files"),
      ]);
      if (agentsResponse.ok) {
        const agents: Agent[] = await agentsResponse.json();
        const found = agents.find((a) => a.id === agentId) ?? null;
        setAgent(found);
        if (found) {
          setName(found.name);
          setInstructions(found.instructions);
          setIsEntryPoint(found.is_entry_point);
        }
      }
      if (attachedResponse.ok) {
        setAttachedFiles(await attachedResponse.json());
      }
      if (allFilesResponse.ok) {
        setAllFiles(await allFilesResponse.json());
      }
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void load();
  }, [agentId]);

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setSaving(true);
    try {
      const response = await backendFetch(`agents/${agentId}`, {
        method: "PATCH",
        body: JSON.stringify({ name, instructions, is_entry_point: isEntryPoint }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(body, "Falha ao salvar — tente novamente."));
        // Reverte o toggle de ponto de entrada pro último valor confirmado —
        // sem isso a caixa fica marcada mesmo com o PATCH tendo falhado.
        if (agent) setIsEntryPoint(agent.is_entry_point);
        return;
      }
      setAgent(body);
      setIsEntryPoint(body.is_entry_point);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setSaving(false);
    }
  }

  async function handleAttach(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedFileId) return;
    setFeedback(null);
    setAttaching(true);
    try {
      const response = await backendFetch(`agents/${agentId}/knowledge-base-files`, {
        method: "POST",
        body: JSON.stringify({ knowledge_base_file_id: selectedFileId }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(extractErrorDetail(body, "Falha ao anexar — tente novamente."));
        return;
      }
      setSelectedFileId("");
      await load();
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setAttaching(false);
    }
  }

  async function handleDetach(file: AttachedFile) {
    if (!window.confirm(`Desanexar "${file.filename}" deste agente?`)) return;
    try {
      const response = await backendFetch(`agents/${agentId}/knowledge-base-files/${file.id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(extractErrorDetail(body, "Falha ao desanexar — tente novamente."));
        return;
      }
      setAttachedFiles(attachedFiles.filter((f) => f.id !== file.id));
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    }
  }

  if (!loaded) {
    return (
      <main className="flex flex-1 items-center justify-center bg-ground text-sm text-muted">
        Carregando...
      </main>
    );
  }

  if (!agent) {
    return (
      <main className="flex flex-1 items-center justify-center bg-ground text-sm text-muted">
        Agente não encontrado.{" "}
        <Link href="/agentes" className="ml-1 text-accent hover:underline">
          Voltar
        </Link>
      </main>
    );
  }

  const attachedIds = new Set(attachedFiles.map((f) => f.id));
  const availableFiles = allFiles.filter((f) => !attachedIds.has(f.id));

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      <header className="border-b border-line px-8 py-5">
        <Link href="/agentes" className="text-xs text-muted hover:text-ink">
          ← Agentes
        </Link>
        <h1 className="font-display text-xl font-semibold text-ink">{agent.name}</h1>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <form onSubmit={handleSave} className="flex max-w-md flex-col gap-4">
          <label className="flex flex-col gap-1 text-sm text-ink">
            Nome
            <input
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-ink">
            Instruções
            <textarea
              required
              rows={8}
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={isEntryPoint}
              onChange={(event) => setIsEntryPoint(event.target.checked)}
            />
            Ponto de entrada (recebe a primeira mensagem de conversas novas)
          </label>
          <button
            type="submit"
            disabled={saving}
            className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
          >
            {saving ? "Salvando..." : "Salvar"}
          </button>
        </form>

        <hr className="my-6 border-line" />

        <h2 className="font-display text-lg font-semibold text-ink">Base de conhecimento</h2>
        <ul className="mt-4 max-w-md">
          {attachedFiles.length === 0 && (
            <li className="py-4 text-sm text-muted">Nenhum arquivo anexado ainda.</li>
          )}
          {attachedFiles.map((file) => (
            <li
              key={file.id}
              className="flex items-center justify-between border-b border-line py-3"
            >
              <p className="truncate text-ink">{file.filename}</p>
              <button
                type="button"
                onClick={() => void handleDetach(file)}
                className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
              >
                Desanexar
              </button>
            </li>
          ))}
        </ul>

        <form onSubmit={handleAttach} className="mt-4 flex max-w-md items-end gap-2">
          <label className="flex flex-1 flex-col gap-1 text-sm text-ink">
            Anexar arquivo já enviado
            <select
              value={selectedFileId}
              onChange={(event) => setSelectedFileId(event.target.value)}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            >
              <option value="">Selecione um arquivo</option>
              {availableFiles.map((file) => (
                <option key={file.id} value={file.id}>
                  {file.filename}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            disabled={attaching || !selectedFileId}
            className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
          >
            {attaching ? "Anexando..." : "Anexar"}
          </button>
        </form>

        <p className="mt-4 text-sm text-muted">
          Ou{" "}
          <Link
            href={`/base-de-conhecimento?agent_id=${agent.id}`}
            className="text-accent hover:underline"
          >
            envie um arquivo novo direto pra este agente
          </Link>
          .
        </p>
      </div>
    </main>
  );
}
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/web && pnpm test -- AgentDetail.test.tsx`
Expected: os 5 testes passam.

- [ ] **Step 5: Criar a página**

Criar `apps/web/src/app/agentes/[id]/page.tsx`:

```tsx
import { AgentDetail } from "@/components/AgentDetail";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default async function AgenteDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="agentes" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <AgentDetail agentId={id} />
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Rodar a suíte completa + build**

Run: `cd apps/web && pnpm test && pnpm lint && pnpm build`
Expected: tudo verde.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/app/agentes/[id]/page.tsx apps/web/src/components/AgentDetail.tsx apps/web/__tests__/AgentDetail.test.tsx
git commit -m "feat(web): página /agentes/[id] — editar agente e gerenciar base de conhecimento"
```

---

### Task 5: `apps/web` — `/base-de-conhecimento` ganha o seletor de agente de destino

**Files:**
- Modify: `apps/web/src/components/KnowledgeBasePanel.tsx`
- Modify: `apps/web/__tests__/KnowledgeBasePanel.test.tsx`

**Interfaces:**
- Consumes: `Agent` (Task 2), `GET /api/v1/agents`, `POST /api/v1/knowledge-base/files` (campo `agent_id` já aceito desde a Etapa 1) — e o link `/base-de-conhecimento?agent_id=...` que a Task 4 já aponta pra aqui.
- Produces: nenhuma interface nova para outras tasks — última task de código deste plano.

- [ ] **Step 1: Escrever os testes que falham**

Substituir TODO o conteúdo de `apps/web/__tests__/KnowledgeBasePanel.test.tsx` por:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { KnowledgeBasePanel } from "@/components/KnowledgeBasePanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const files = [
  {
    id: "f1",
    filename: "regimento.pdf",
    size_bytes: 1048576,
    mime_type: "application/pdf",
    status: "ready",
    error_message: null,
    uploaded_at: "2026-07-08T12:00:00Z",
  },
  {
    id: "f2",
    filename: "contrato.docx",
    size_bytes: 2048,
    mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    status: "error",
    error_message: "Falha na ingestão (HTTP 400)",
    uploaded_at: "2026-07-08T11:00:00Z",
  },
];

const agents = [
  {
    id: "a1",
    name: "Secretária",
    instructions: "x",
    is_entry_point: true,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
  },
  {
    id: "a2",
    name: "Condominial",
    instructions: "y",
    is_entry_point: false,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
  },
];

function mockRouting(uploadHandler?: (init: RequestInit) => unknown) {
  mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "agents") return { ok: true, status: 200, json: async () => agents };
    if (path === "knowledge-base/files" && init?.method === "POST") {
      return uploadHandler
        ? uploadHandler(init)
        : { ok: true, status: 202, json: async () => files[0] };
    }
    if (path === "knowledge-base/files") return { ok: true, status: 200, json: async () => files };
    return { ok: true, status: 200, json: async () => null };
  });
}

describe("KnowledgeBasePanel", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
    window.history.pushState({}, "", "/base-de-conhecimento");
  });

  it("lista os arquivos com status", async () => {
    mockRouting();

    render(<KnowledgeBasePanel pollMs={0} />);

    await waitFor(() => expect(screen.getByText("regimento.pdf")).toBeInTheDocument());
    expect(screen.getByText("contrato.docx")).toBeInTheDocument();
    expect(screen.getByText(/pronto/i)).toBeInTheDocument();
    expect(screen.getByText(/Falha na ingestão/)).toBeInTheDocument();
  });

  it("pré-seleciona o agente ponto de entrada por padrão", async () => {
    mockRouting();

    render(<KnowledgeBasePanel pollMs={0} />);

    await waitFor(() => expect(screen.getByLabelText("Agente de destino")).toHaveValue("a1"));
  });

  it("pré-seleciona o agente vindo da URL (?agent_id=)", async () => {
    window.history.pushState({}, "", "/base-de-conhecimento?agent_id=a2");
    mockRouting();

    render(<KnowledgeBasePanel pollMs={0} />);

    await waitFor(() => expect(screen.getByLabelText("Agente de destino")).toHaveValue("a2"));
  });

  it("envia o agent_id selecionado no FormData do upload", async () => {
    let capturedForm: FormData | null = null;
    mockRouting((init) => {
      capturedForm = init.body as FormData;
      return { ok: true, status: 202, json: async () => files[0] };
    });

    render(<KnowledgeBasePanel pollMs={0} />);
    await waitFor(() => expect(screen.getByLabelText("Agente de destino")).toHaveValue("a1"));

    fireEvent.change(screen.getByLabelText("Agente de destino"), { target: { value: "a2" } });
    const file = new File(["conteudo"], "novo.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Enviar arquivo"), { target: { files: [file] } });

    await waitFor(() => expect(capturedForm).not.toBeNull());
    expect(capturedForm!.get("agent_id")).toBe("a2");
  });

  it("mostra erro se tentar enviar sem nenhum agente disponível", async () => {
    mockedFetch.mockImplementation(async (path: string) => {
      if (path === "agents") return { ok: true, status: 200, json: async () => [] };
      if (path === "knowledge-base/files") return { ok: true, status: 200, json: async () => [] };
      return { ok: true, status: 200, json: async () => null };
    });

    render(<KnowledgeBasePanel pollMs={0} />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalledWith("agents"));

    const file = new File(["conteudo"], "novo.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Enviar arquivo"), { target: { files: [file] } });

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/escolha o agente/i));
  });
});
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `cd apps/web && pnpm test -- KnowledgeBasePanel.test.tsx`
Expected: FAIL em todos os testes novos — o componente ainda não busca `"agents"` nem tem um `<select>` com label `"Agente de destino"`.

- [ ] **Step 3: Implementar**

Substituir TODO o conteúdo de `apps/web/src/components/KnowledgeBasePanel.tsx` por:

```tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type { Agent } from "@/lib/types";

type KbFile = {
  id: string;
  filename: string;
  size_bytes: number;
  mime_type: string;
  status: "processing" | "ready" | "error";
  error_message: string | null;
  uploaded_at: string;
};

const ACCEPTED = ".pdf,.docx,.txt";
const MAX_FILE_BYTES = 20 * 1024 * 1024;

const STATUS_LABEL: Record<KbFile["status"], string> = {
  processing: "processando",
  ready: "pronto",
  error: "erro",
};

const STATUS_CLASS: Record<KbFile["status"], string> = {
  processing: "bg-brass-soft text-brass",
  ready: "bg-accent-soft text-accent",
  error: "bg-danger/10 text-danger",
};

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

export function KnowledgeBasePanel({ pollMs = 5000 }: { pollMs?: number }) {
  const [files, setFiles] = useState<KbFile[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const response = await backendFetch("knowledge-base/files");
      if (response.ok) {
        setFiles(await response.json());
      }
    } catch {
      // rede indisponível: mantém a lista atual e tenta no próximo ciclo
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    async function loadAgents() {
      try {
        const response = await backendFetch("agents");
        if (!response.ok) return;
        const body: Agent[] = await response.json();
        setAgents(body);

        const fromUrl = new URLSearchParams(window.location.search).get("agent_id");
        if (fromUrl && body.some((a) => a.id === fromUrl)) {
          setSelectedAgentId(fromUrl);
          return;
        }
        const entryPoint = body.find((a) => a.is_entry_point);
        if (entryPoint) setSelectedAgentId(entryPoint.id);
      } catch {
        // fail-safe: sem agentes carregados, o select fica vazio e o upload exige escolha manual
      }
    }
    void loadAgents();
  }, []);

  const hasProcessing = files.some((file) => file.status === "processing");

  useEffect(() => {
    if (!pollMs || !hasProcessing) return;
    const interval = setInterval(() => void load(), pollMs);
    return () => clearInterval(interval);
  }, [load, pollMs, hasProcessing]);

  async function handleUpload(selected: File) {
    setFeedback(null);
    const extension = selected.name.slice(selected.name.lastIndexOf(".")).toLowerCase();
    if (![".pdf", ".docx", ".txt"].includes(extension)) {
      setFeedback("Formato não suportado — envie PDF, DOCX ou TXT.");
      return;
    }
    if (selected.size > MAX_FILE_BYTES) {
      setFeedback("Arquivo excede o limite de 20 MB.");
      return;
    }
    if (!selectedAgentId) {
      setFeedback("Escolha o agente de destino antes de enviar.");
      return;
    }

    const form = new FormData();
    form.append("file", selected);
    form.append("agent_id", selectedAgentId);
    setUploading(true);
    try {
      const response = await backendFetch("knowledge-base/files", {
        method: "POST",
        body: form,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(body?.detail ?? "Falha no upload — tente novamente.");
        return;
      }
      await load();
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function handleDelete(file: KbFile) {
    if (!window.confirm(`Excluir "${file.filename}" da base de conhecimento?`)) return;
    try {
      const response = await backendFetch(`knowledge-base/files/${file.id}`, { method: "DELETE" });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(body?.detail ?? "Falha ao excluir — tente novamente.");
        return;
      }
      await load();
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    }
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      <header className="flex items-center justify-between border-b border-line px-8 py-5">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">Base de conhecimento</h1>
          <p className="text-sm text-muted">
            PDF, DOCX ou TXT, até 20 MB — cada arquivo fica anexado a um agente específico.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted">
            Agente de destino
            <select
              required
              value={selectedAgentId}
              onChange={(event) => setSelectedAgentId(event.target.value)}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            >
              <option value="" disabled>
                Selecione um agente
              </option>
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
            </select>
          </label>
          <label
            className={`cursor-pointer rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent ${uploading ? "pointer-events-none opacity-50" : ""}`}
          >
            {uploading ? "Enviando..." : "Enviar arquivo"}
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPTED}
              className="hidden"
              onChange={(event) => {
                const selected = event.target.files?.[0];
                if (selected) void handleUpload(selected);
              }}
            />
          </label>
        </div>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <ul className="flex-1 overflow-y-auto px-8 py-4">
        {files.length === 0 && (
          <li className="py-10 text-center text-sm text-muted">
            Nenhum arquivo na base de conhecimento ainda.
          </li>
        )}
        {files.map((file) => (
          <li
            key={file.id}
            className="flex items-center gap-4 border-b border-line py-4 last:border-b-0"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium text-ink">{file.filename}</p>
              <p className="text-xs text-muted">
                {formatSize(file.size_bytes)} ·{" "}
                {new Date(file.uploaded_at).toLocaleDateString("pt-BR")}
              </p>
              {file.status === "error" && file.error_message && (
                <p className="mt-1 text-xs text-danger">{file.error_message}</p>
              )}
            </div>
            <span
              className={`rounded-full px-3 py-1 font-mono text-[10px] uppercase tracking-[0.15em] ${STATUS_CLASS[file.status]}`}
            >
              {STATUS_LABEL[file.status]}
            </span>
            <button
              type="button"
              onClick={() => void handleDelete(file)}
              disabled={file.status === "processing"}
              className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger disabled:opacity-40"
            >
              Excluir
            </button>
          </li>
        ))}
      </ul>
    </main>
  );
}
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/web && pnpm test -- KnowledgeBasePanel.test.tsx`
Expected: os 5 testes passam.

- [ ] **Step 5: Rodar a suíte completa + build**

Run: `cd apps/web && pnpm test && pnpm lint && pnpm build`
Expected: tudo verde.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/KnowledgeBasePanel.tsx apps/web/__tests__/KnowledgeBasePanel.test.tsx
git commit -m "feat(web): upload de base de conhecimento ganha o seletor de agente de destino"
```

---

### Task 6: Documentação — `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: comportamento final das Tasks 1-5 (deve ser a última task).
- Produces: nenhum código — só documentação, sem passo de teste.

- [ ] **Step 1: Atualizar a seção "Agentes por Tenant"**

Substituir a última linha da seção `## Agentes por Tenant (\`apps/api\`) — ✅ implementado` (que hoje diz `- Pendência (Etapa 3, ainda não iniciada): painel (\`apps/web\`) pra listar/criar/editar agentes...`) por:

```markdown
- ✅ **Painel do escritório (Etapa 3, 2026-07)**: `/agentes` (listar, criar, apagar — com a mesma validação de 409 do backend refletida na UI) e `/agentes/{id}` (editar nome/instruções/ponto de entrada, anexar/desanexar arquivos de KB já enviados, atalho pra upload direto já pré-selecionando aquele agente via `/base-de-conhecimento?agent_id=`). O upload em `/base-de-conhecimento` ganhou um seletor de agente de destino (pré-selecionado com o ponto de entrada, ou com o agente vindo da URL) — o campo continua opcional no backend (fallback pro ponto de entrada), a obrigatoriedade é só de UX. Endpoint novo `GET /api/v1/agents/{id}/knowledge-base-files` lista os arquivos anexados a um agente (não existia — só o vínculo em si); o attach duplicado do mesmo arquivo no mesmo agente passou a devolver `409` (era `500`).
```

- [ ] **Step 2: Atualizar a seção Frontend**

Na seção `## Frontend (\`apps/web\`) — páginas e funcionalidades`, adicionar uma entrada nova depois do bloco de `/base-de-conhecimento` (antes de `/creditos`):

```markdown
- **`/agentes`** e **`/agentes/[id]`** — ✅ implementadas: gestão dos agentes de IA do escritório (ver seção "Agentes por Tenant"). `/agentes` lista os agentes (badge "ponto de entrada"), cria novos (nome + instruções + checkbox de ponto de entrada) e apaga (recusa refletida na UI quando é o ponto de entrada ou o único agente). `/agentes/{id}` edita nome/instruções/ponto de entrada e gerencia a base de conhecimento anexada (anexar um arquivo já enviado, desanexar, ou atalho pro upload direto). `AgentsPanel`/`AgentDetail`, sem Server Actions (mesmo padrão de `EndCustomerBillingPanel`).
```

E atualizar a linha do bullet de `/base-de-conhecimento` (que hoje termina em `...nome duplicado → erro 409 exibido)`), acrescentando a menção do seletor de agente:

```markdown
- **`/base-de-conhecimento`** — ✅ implementada: gestão da base de conhecimento própria do escritório, com o upload direcionado a um agente específico (seletor de agente de destino, pré-selecionado com o ponto de entrada ou com o agente vindo de `?agent_id=` — ver `/agentes/{id}`).
```

(mantendo o restante do parágrafo — os sub-bullets de API/Limites/Nome duplicado/Ingestão/Front — intocado, só a frase de abertura ganha essa menção.)

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: atualiza CLAUDE.md pra Etapa 3 (frontend de agentes por tenant)"
```
