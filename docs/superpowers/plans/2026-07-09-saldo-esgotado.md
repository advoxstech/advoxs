# Bloqueio do Agente por Saldo Esgotado Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quando `tenants.credit_balance <= 0`, o `worker` para de acionar o agente automático (silêncio total pro cliente final, sem debitar), e o painel do escritório mostra um banner de aviso linkando pra `/creditos`.

**Architecture:** `process_inbound_message` passa a carregar `credit_balance` como parte do contexto já buscado por `_load_context`, e retorna antes de chamar o `agents` service quando o saldo está esgotado. No `web`, um componente `LowBalanceBanner` consulta `GET billing/balance` (já existente) e se auto-renderiza (ou não) nas páginas do painel do tenant.

**Tech Stack:** SQLAlchemy Core + Arq (worker), Next.js 15 App Router + React (web).

## Global Constraints

- **Limite: `credit_balance <= 0`, sem buffer.** A checagem acontece uma vez, no início do processamento, antes de saber quanto a execução vai custar.
- **Reação ao cliente final: silêncio total.** Nenhuma mensagem é enviada via WhatsApp quando bloqueado — o `worker` não ganha capacidade de enviar WhatsApp nesta entrega. A mensagem do contato já foi persistida pelo `api` antes de enfileirar; fica visível em `/conversas` esperando um humano.
- **Checagem antes de chamar `send_message_to_agents`** — evita custo de LLM numa execução que não vai ser cobrada.
- **Sem mudança de schema** — `tenants.credit_balance` já existe e já está mapeado em `apps/worker/app/tables.py`.
- **Banner reaproveita `GET /api/v1/billing/balance`** (já implementado, autenticado, tenant-scoped) — sem endpoint novo, sem e-mail.
- **Banner omitido em `/creditos`** — o saldo já é mostrado ali; seria redundante.
- Mensagens/comentários em pt-BR com acentuação correta.
- Comandos: `apps/worker` → `uv run pytest tests/unit`, `uv run ruff check .`, `uv run ruff format --check .`. `apps/web` → `pnpm test`, `pnpm lint`, `pnpm build` (via `npx --yes pnpm@9 <comando>` se `pnpm` não estiver disponível globalmente).

---

### Task 1: `worker` — bloquear o agente quando o saldo está esgotado

**Files:**
- Modify: `apps/worker/app/tasks/messages.py`
- Modify: `apps/worker/tests/unit/test_process_inbound_message.py`

**Interfaces:**
- Consumes: `tables.tenants` (`apps/worker/app/tables.py`, já existente, coluna `credit_balance` já mapeada).
- Produces: `InboundContext` ganha o campo `credit_balance: int`; `process_inbound_message` retorna antecipadamente sem chamar `send_message_to_agents` quando `inbound.credit_balance <= 0`.

- [ ] **Step 1: Escrever os testes que falham**

Em `apps/worker/tests/unit/test_process_inbound_message.py`, trocar a função `_inbound` (que hoje não recebe `credit_balance`) por:

```python
def _inbound(state: str = "agent", credit_balance: int = 1000) -> InboundContext:
    return InboundContext(
        conversation_state=state,
        contact_phone_number="5511888888888",
        message_content="Olá",
        phone_number_id="PNID",
        access_token_encrypted="token-cifrado",
        credit_balance=credit_balance,
    )
```

Adicionar, após `test_human_state_skips_agent` (mesmo nível de indentação, no módulo):

```python
async def test_saldo_esgotado_nao_chama_agente(patched) -> None:
    patched["load"].return_value = _inbound(credit_balance=0)

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_not_awaited()
    patched["persist"].assert_not_awaited()
    patched["debitar"].assert_not_awaited()


async def test_saldo_negativo_nao_chama_agente(patched) -> None:
    patched["load"].return_value = _inbound(credit_balance=-50)

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_not_awaited()


async def test_saldo_positivo_chama_agente_normalmente(patched) -> None:
    patched["load"].return_value = _inbound(credit_balance=1)

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()
```

- [ ] **Step 2: Rodar os testes e ver falhar**

Run: `cd apps/worker && uv run pytest tests/unit/test_process_inbound_message.py -v`
Expected: FAIL — `TypeError: InboundContext.__init__() got an unexpected keyword argument 'credit_balance'` (a dataclass ainda não tem esse campo).

- [ ] **Step 3: Adicionar `credit_balance` ao `InboundContext` e carregar no `_load_context`**

Em `apps/worker/app/tasks/messages.py`, trocar o dataclass:

```python
@dataclass
class InboundContext:
    conversation_state: str
    contact_phone_number: str
    message_content: str
    phone_number_id: str
    access_token_encrypted: str
    credit_balance: int
```

Em `_load_context`, adicionar a consulta do saldo do tenant (após a consulta de `number`, antes do `return`):

```python
    credit_balance = (
        await session.execute(
            select(tables.tenants.c.credit_balance).where(
                tables.tenants.c.id == uuid.UUID(tenant_id)
            )
        )
    ).scalar_one()

    return InboundContext(
        conversation_state=conversation.state,
        contact_phone_number=conversation.contact_phone_number,
        message_content=content,
        phone_number_id=number.phone_number_id,
        access_token_encrypted=number.access_token_encrypted,
        credit_balance=credit_balance,
    )
```

- [ ] **Step 4: Bloquear a chamada ao agente quando o saldo está esgotado**

Em `process_inbound_message`, adicionar a checagem logo depois do bloco `if inbound.conversation_state != "agent":` (mesmo `if inbound is None: return` já existente antes):

```python
    if inbound.conversation_state != "agent":
        # Takeover humano: a mensagem só aparece no painel de conversas.
        logger.info(
            "Conversa em modo humano, agente não acionado | tenant=%s conversation=%s",
            tenant_id,
            conversation_id,
        )
        return

    if inbound.credit_balance <= 0:
        # Saldo esgotado: silêncio total pro cliente final — a mensagem só
        # aparece no painel de conversas, aguardando um humano do escritório.
        logger.info(
            "Saldo esgotado, agente não acionado | tenant=%s conversation=%s saldo=%s",
            tenant_id,
            conversation_id,
            inbound.credit_balance,
        )
        return

    access_token = decrypt_access_token(inbound.access_token_encrypted)
```

- [ ] **Step 5: Rodar os testes e ver passar**

Run: `cd apps/worker && uv run pytest tests/unit -v`
Expected: PASS em todos, incluindo os 3 novos e os já existentes (que agora usam `credit_balance=1000` por default via `_inbound()`).

- [ ] **Step 6: Rodar lint**

Run: `cd apps/worker && uv run ruff check . && uv run ruff format --check .`
Expected: sem erros.

- [ ] **Step 7: Commit**

```bash
git add apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_process_inbound_message.py
git commit -m "feat(worker): bloquear o agente automático quando o saldo de créditos está esgotado"
```

---

### Task 2: `web` — banner de saldo esgotado no painel do tenant

**Files:**
- Create: `apps/web/src/components/LowBalanceBanner.tsx`
- Modify: `apps/web/src/app/conversas/page.tsx`
- Modify: `apps/web/src/app/base-de-conhecimento/page.tsx`
- Modify: `apps/web/src/app/configuracoes/whatsapp/page.tsx`
- Test: `apps/web/__tests__/LowBalanceBanner.test.tsx`

**Interfaces:**
- Consumes: `backendFetch` (`@/lib/client-api`, já existente); `GET billing/balance` (já existente, devolve `{credit_balance: number}`).
- Produces: `LowBalanceBanner()` (sem props) em `@/components/LowBalanceBanner`.

- [ ] **Step 1: Escrever o teste que falha**

Criar `apps/web/__tests__/LowBalanceBanner.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("LowBalanceBanner", () => {
  it("mostra o aviso quando o saldo está esgotado (0)", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ credit_balance: 0 }) });

    render(<LowBalanceBanner />);

    await waitFor(() =>
      expect(screen.getByText(/saldo de créditos está esgotado/i)).toBeInTheDocument(),
    );
    expect(screen.getByRole("link", { name: /comprar créditos/i })).toHaveAttribute(
      "href",
      "/creditos",
    );
  });

  it("mostra o aviso quando o saldo está negativo", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ credit_balance: -50 }) });

    render(<LowBalanceBanner />);

    await waitFor(() =>
      expect(screen.getByText(/saldo de créditos está esgotado/i)).toBeInTheDocument(),
    );
  });

  it("não mostra nada quando o saldo é positivo", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ credit_balance: 500 }) });

    render(<LowBalanceBanner />);

    await waitFor(() => expect(mockedFetch).toHaveBeenCalledWith("billing/balance"));
    expect(screen.queryByText(/saldo de créditos está esgotado/i)).not.toBeInTheDocument();
  });

  it("não quebra quando a busca de saldo falha (fail-safe silencioso)", async () => {
    mockedFetch.mockRejectedValue(new Error("network error"));

    render(<LowBalanceBanner />);

    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    expect(screen.queryByText(/saldo de créditos está esgotado/i)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/web && npx --yes pnpm@9 test -- LowBalanceBanner`
Expected: FAIL — `@/components/LowBalanceBanner` não existe.

- [ ] **Step 3: Criar `LowBalanceBanner`**

Criar `apps/web/src/components/LowBalanceBanner.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";

export function LowBalanceBanner() {
  const [lowBalance, setLowBalance] = useState(false);

  useEffect(() => {
    async function loadBalance() {
      try {
        const response = await backendFetch("billing/balance");
        if (response.ok) {
          const body = await response.json();
          setLowBalance(body.credit_balance <= 0);
        }
      } catch {
        // Fail-safe silencioso — sem saldo confirmado, não exibe o aviso.
      }
    }
    void loadBalance();
  }, []);

  if (!lowBalance) return null;

  return (
    <div className="flex items-center justify-between gap-4 border-b border-danger bg-danger/10 px-6 py-2.5 text-sm text-danger">
      <span>
        Seu saldo de créditos está esgotado — o atendimento automático está pausado.
      </span>
      <Link href="/creditos" className="font-medium underline hover:no-underline">
        Comprar créditos
      </Link>
    </div>
  );
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/web && npx --yes pnpm@9 test -- LowBalanceBanner`
Expected: PASS (4/4).

- [ ] **Step 5: Usar `LowBalanceBanner` nas 3 páginas do painel do tenant**

Substituir `apps/web/src/app/conversas/page.tsx` por:

```tsx
import { ConversationsPanel } from "@/components/ConversationsPanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default function ConversasPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="conversas" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <ConversationsPanel />
      </div>
    </div>
  );
}
```

Substituir `apps/web/src/app/base-de-conhecimento/page.tsx` por:

```tsx
import { KnowledgeBasePanel } from "@/components/KnowledgeBasePanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default function BaseDeConhecimentoPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="base" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <KnowledgeBasePanel />
      </div>
    </div>
  );
}
```

Substituir `apps/web/src/app/configuracoes/whatsapp/page.tsx` por:

```tsx
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";
import { WhatsAppConnectionPanel } from "@/components/WhatsAppConnectionPanel";

export default function ConfiguracoesWhatsAppPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="config" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <WhatsAppConnectionPanel />
      </div>
    </div>
  );
}
```

Nota: `/creditos/page.tsx` **não** é modificada nesta task — o banner é intencionalmente omitido ali (o saldo já é mostrado na própria página).

- [ ] **Step 6: Rodar toda a suíte, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde — as 3 páginas continuam renderizando os painéis (que já têm `flex-1` internamente, funcionam como filho do novo `<div className="flex flex-1 flex-col overflow-hidden">`).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/components/LowBalanceBanner.tsx apps/web/src/app/conversas/page.tsx apps/web/src/app/base-de-conhecimento/page.tsx apps/web/src/app/configuracoes/whatsapp/page.tsx apps/web/__tests__/LowBalanceBanner.test.tsx
git commit -m "feat(web): banner de saldo esgotado no painel do escritório"
```

---

### Task 3: Atualizar `CLAUDE.md` e verificação local

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: tudo das tasks anteriores.

- [ ] **Step 1: Atualizar o CLAUDE.md**

- Seção "Billing / Créditos": trocar "⚠️ O saldo **pode negativar** hoje — o comportamento quando zera segue pendente (bloquear? avisar?), ver pendências de billing." por um parágrafo ✅ descrevendo a regra implementada: `worker` bloqueia o agente quando `credit_balance <= 0` (checagem antes de chamar o `agents`, sem debitar), silêncio total pro cliente final (mensagem fica em `/conversas` esperando humano), banner de aviso no painel (`LowBalanceBanner`, reaproveita `GET billing/balance`, omitido em `/creditos`).
- Seção "Pendências de billing": remover o item "Comportamento quando o saldo de créditos zera" da lista — resolvido.
- Seção "Pendências / próximos tópicos a detalhar": remover "Comportamento quando o saldo de créditos zera" também de lá.

- [ ] **Step 2: Build e verificação local**

```bash
docker compose up -d --build worker api web
```

1. Login com o tenant de seed (`admin@demo.com`/`segredo123`), confirmar saldo atual via `GET /api/v1/billing/balance`.
2. Se o saldo estiver positivo, zerar manualmente via `psql` só para o teste local: `UPDATE tenants SET credit_balance = 0 WHERE email_contato = 'admin@demo.com';` (dentro do container `postgres`, banco `advoxs`).
3. Mandar uma mensagem real pelo webhook (ou simular via `curl` no endpoint de webhook, se disponível localmente) e confirmar nos logs do `worker` (`docker compose logs worker`) a linha `"Saldo esgotado, agente não acionado"` — sem chamada ao `agents` nem débito.
4. Confirmar via `psql` que **nenhuma** linha nova apareceu em `messages` com `sender_type=agent` nem em `credit_transactions` para esse tenant depois do passo 3.
5. Acessar `http://localhost:3001/conversas` logado — confirmar que o banner "Seu saldo de créditos está esgotado" aparece no topo, com o link "Comprar créditos" levando pra `/creditos`.
6. Acessar `/base-de-conhecimento` e `/configuracoes/whatsapp` — confirmar que o banner aparece igual nas duas.
7. Acessar `/creditos` — confirmar que o banner **não** aparece ali (por design).
8. Restaurar o saldo do tenant de seed via `psql` (`UPDATE tenants SET credit_balance = <valor original> WHERE email_contato = 'admin@demo.com';`) para não deixar o ambiente de dev num estado inconsistente para testes futuros.
9. Recarregar `/conversas` — confirmar que o banner desaparece.

Expected: todos os passos funcionam; o passo 4 (nada persistido pro cliente/débito) é o mais importante — prova que o silêncio total realmente não gera custo nem resposta.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: bloqueio do agente por saldo esgotado documentado no CLAUDE.md"
```
