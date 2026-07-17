# Etapa 2 — Consumo Ponderado e Moeda Única — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trocar a conversão `ceil(tokens_used/env)` pela ponderação input/output lida da `pricing_configs` (débito fracionado, 4 casas), eliminar a race de débito concorrente com `SELECT ... FOR UPDATE`, e virar a **moeda única**: com a cobrança do cliente final habilitada e saldo positivo, o consumo debita **só** a wallet do cliente final (fim da dupla cobrança — o tenant paga na revenda, Etapa 3).

**Architecture:** Cálculo compartilhado por app (`calcular_creditos` duplicada em `worker` e `api`, mesmo padrão da env antiga): `creditos = round((input×input_weight + output×output_weight) / tokens_per_credit, 4)` com `ROUND_HALF_UP`; fallback pró-plataforma quando o breakdown vem zerado (agents antigo): `tokens_used` inteiro tratado como output. O débito lê a config vigente dentro da própria transação, trava a linha da wallet (`FOR UPDATE`) e grava `pricing_config_id` no lançamento. Schemas de crédito viram `float` e o front formata com `pt-BR`.

**Tech Stack:** os mesmos da Etapa 1 + Vitest (web).

## Global Constraints

- Proporção e pesos **sempre** da `pricing_configs` — as envs `CREDIT_TOKENS_PER_CREDIT` deixam de ser lidas (atributo removido dos dois configs).
- Arredondamento: `Decimal.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)` — nunca `ceil`, nunca float no cálculo.
- **Fallback de transição** (agents antigo sem breakdown): `tokens_input+tokens_output == 0` e `tokens_used > 0` → tratar `tokens_used` inteiro como output (peso 1.0, cobra mais, nunca menos).
- **Moeda única**: `charged_to_customer = enabled AND end_customer_balance > 0` (saldo lido antes da chamada, como hoje) → debita SÓ o cliente final; senão debita SÓ o tenant. `end_customer_tokens_per_credit` deixa de ser lido (coluna fica, deprecada).
- **Gate de saldo do worker muda de propósito**: turno custeado pelo cliente final (enabled + saldo > 0) roda **mesmo com o tenant zerado** — o crédito do cliente já saiu do estoque do tenant na revenda. Silêncio total só quando o turno seria custeado pelo tenant E `tenant.credit_balance <= 0`.
- Todo lançamento de consumo grava `pricing_config_id` da config usada.
- `FOR UPDATE` na linha da wallet dentro da transação de débito (tenants / end_customer_balances), mantendo o update relativo.
- Commits Conventional em pt-BR; mesmos comandos de teste/lint por app; `pnpm test`/`pnpm lint` no web.
- ⚠️ Arquivos com formatação pré-existente não commitada (`apps/api/app/api/v1/end_customer_billing.py`, `apps/api/tests/unit/test_end_customer_billing_service.py`, `.claude/settings.local.json`) ficam fora dos commits.

---

### Task 1: `worker` — pricing (leitura da config + cálculo ponderado)

**Files:**
- Modify: `apps/worker/app/tables.py` (tabela `pricing_configs`)
- Create: `apps/worker/app/pricing.py`
- Modify: `apps/worker/app/config.py` (remover `credit_tokens_per_credit`)
- Test: `apps/worker/tests/unit/test_pricing.py` (novo), `apps/worker/tests/unit/test_worker_settings.py` (se referenciar a env)

**Interfaces:**
- Produces: `async get_current_pricing_config(session) -> Row` (colunas `id`, `tokens_per_credit`, `input_weight`, `output_weight`; `RuntimeError` se vazia) e `calcular_creditos(tokens_input: int, tokens_output: int, tokens_used: int, config) -> Decimal` — Task 2 consome ambas.

- [ ] **Step 1:** Adicionar em `tables.py`:

```python
pricing_configs = Table(
    "pricing_configs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tokens_per_credit", Integer),
    Column("input_weight", Numeric(6, 4)),
    Column("output_weight", Numeric(6, 4)),
    Column("effective_at", DateTime(timezone=True)),
)
```

- [ ] **Step 2:** Escrever `tests/unit/test_pricing.py`:

```python
from decimal import Decimal
from types import SimpleNamespace

from app.pricing import calcular_creditos

CONFIG = SimpleNamespace(
    tokens_per_credit=1000, input_weight=Decimal("0.3"), output_weight=Decimal("1.0")
)


def test_pondera_input_e_output():
    # 2000*0.3 + 500*1.0 = 1100 tokens ponderados -> 1.1 créditos
    assert calcular_creditos(2000, 500, 2500, CONFIG) == Decimal("1.1000")


def test_arredonda_para_4_casas_half_up():
    # 1*0.3 = 0.3 -> 0.0003 créditos; 166*0.3=49.8 -> 0.0498
    assert calcular_creditos(1, 0, 1, CONFIG) == Decimal("0.0003")
    assert calcular_creditos(166, 0, 166, CONFIG) == Decimal("0.0498")


def test_fallback_sem_breakdown_trata_tudo_como_output():
    # agents antigo: breakdown zerado mas tokens_used > 0 -> peso 1.0
    assert calcular_creditos(0, 0, 3500, CONFIG) == Decimal("3.5000")


def test_zero_tokens_zero_creditos():
    assert calcular_creditos(0, 0, 0, CONFIG) == Decimal("0.0000")
```

- [ ] **Step 3:** Rodar e ver falhar (`ModuleNotFoundError: app.pricing`).

- [ ] **Step 4:** Implementar `apps/worker/app/pricing.py`:

```python
"""Config global de pricing + conversão de tokens ponderados em créditos.

Espelha apps/api/app/services/pricing.py (codebases separados, mesmo padrão
da antiga env duplicada). A config vigente é a de effective_at mais recente
já alcançado — a migration 0013 seeda a inicial.
"""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import tables

_PRECISION = Decimal("0.0001")


async def get_current_pricing_config(session: AsyncSession):
    config = (
        await session.execute(
            select(
                tables.pricing_configs.c.id,
                tables.pricing_configs.c.tokens_per_credit,
                tables.pricing_configs.c.input_weight,
                tables.pricing_configs.c.output_weight,
            )
            .where(tables.pricing_configs.c.effective_at <= datetime.now(UTC))
            .order_by(tables.pricing_configs.c.effective_at.desc())
            .limit(1)
        )
    ).one_or_none()
    if config is None:
        raise RuntimeError(
            "Nenhuma pricing_config vigente — rode as migrations (0013 seeda a inicial)"
        )
    return config


def calcular_creditos(tokens_input: int, tokens_output: int, tokens_used: int, config) -> Decimal:
    """Créditos fracionados (4 casas, HALF_UP) a partir dos tokens ponderados.

    Fallback de transição: breakdown zerado com tokens_used > 0 (agents antigo)
    trata tudo como output — cobra a mais, nunca a menos."""
    if not tokens_input and not tokens_output and tokens_used:
        tokens_output = tokens_used
    ponderados = (
        Decimal(tokens_input) * config.input_weight
        + Decimal(tokens_output) * config.output_weight
    )
    return (ponderados / Decimal(config.tokens_per_credit)).quantize(
        _PRECISION, rounding=ROUND_HALF_UP
    )
```

- [ ] **Step 5:** Remover `credit_tokens_per_credit: int = 1000` de `apps/worker/app/config.py` (checar `test_worker_settings.py` e remover assert correspondente, se houver).

- [ ] **Step 6:** `uv run pytest tests/unit/test_pricing.py -v` → PASS. (A suíte inteira ainda quebra em `messages.py` — Task 2 conserta; commit conjunto no fim da Task 2.)

---

### Task 2: `worker` — débito ponderado, moeda única e FOR UPDATE

**Files:**
- Modify: `apps/worker/app/tasks/messages.py`
- Test: `apps/worker/tests/unit/test_process_inbound_message.py`, `apps/worker/tests/unit/test_debitar_creditos_cliente_final.py`, `apps/worker/tests/unit/test_load_context.py` (se referenciar `end_customer_tokens_per_credit`), `apps/worker/tests/unit/test_persist_agent_responses.py` (tipo de credits)

**Interfaces:**
- Consumes: `get_current_pricing_config` / `calcular_creditos` (Task 1).
- Produces: novo fluxo de débito — `_debitar_creditos(session, tenant_id, message_id, tokens_used, credits: Decimal, tokens_input, tokens_output, pricing_config_id)` e `_debitar_creditos_cliente_final(... mesmos params + contact_phone_number ...)`, ambos com `SELECT ... FOR UPDATE` na wallet antes do update relativo e `pricing_config_id` no insert. `InboundContext` perde `end_customer_tokens_per_credit`.

- [ ] **Step 1:** Atualizar o fluxo em `process_inbound_message`:
  - Gate de saldo (linha ~111) vira:
    ```python
    customer_funded = inbound.end_customer_billing_enabled and inbound.end_customer_balance > 0
    if inbound.credit_balance <= 0 and not customer_funded:
        ... (silêncio, como hoje)
    ```
  - Bloco de débito (dentro da transação final):
    ```python
    async with open_tenant_session(session_factory, tenant_id) as session:
        config = await get_current_pricing_config(session)
        credits = calcular_creditos(tokens_input, tokens_output, tokens_used, config)
        first_message_id = await _persist_agent_responses(...)
        if credits and first_message_id is not None:
            if customer_funded:
                await _debitar_creditos_cliente_final(..., credits, tokens_input, tokens_output, config.id)
            else:
                await _debitar_creditos(..., credits, tokens_input, tokens_output, config.id)
        await session.commit()
    ```
  - Remover `math.ceil`/`settings.credit_tokens_per_credit` e o bloco separado do débito do cliente final (`end_customer_credits`).
  - `_load_context`/`InboundContext`: remover `end_customer_tokens_per_credit`.

- [ ] **Step 2:** `_debitar_creditos` e `_debitar_creditos_cliente_final` ganham `pricing_config_id` e o lock:

```python
    # Trava a linha da wallet: débitos concorrentes serializam aqui.
    await session.execute(
        select(tables.tenants.c.credit_balance)
        .where(tables.tenants.c.id == uuid.UUID(tenant_id))
        .with_for_update()
    )
```
(equivalente em `end_customer_balances` por `(tenant_id, contact_phone_number)`), seguido do insert no ledger (com `pricing_config_id=pricing_config_id`) e do update relativo já existente.

- [ ] **Step 3:** Atualizar os testes:
  - `test_consumo_convertido_em_creditos_com_ceil` → renomear para `test_consumo_ponderado_fracionado`; mocks de `send` devolvem breakdown; monkeypatch de `get_current_pricing_config` (SimpleNamespace com pesos 0.3/1.0, tokens_per_credit=1000, id=uuid); asserts com Decimal fracionado.
  - Testes do gate: novo caso `test_tenant_zerado_mas_cliente_final_com_saldo_roda_o_agente` e `test_moeda_unica_debita_so_o_cliente_final` (com saldo do cliente > 0, `_debitar_creditos` NÃO é chamado e `_debitar_creditos_cliente_final` É).
  - `test_debitar_creditos_cliente_final.py`: novos params (`pricing_config_id`), asserts do insert incluem `pricing_config_id`; `FakeSession` precisa tolerar o SELECT do lock (guardar só params de insert/update — statements sem `.compile().params` de insert continuam ok porque select também compila; filtrar por presença de chave `amount_credits`/`tokens_input` ou registrar tudo e indexar).

- [ ] **Step 4:** `uv run pytest tests/unit -v` + `uv run ruff check .` → PASS.

- [ ] **Step 5:** Commit:
```bash
git add apps/worker/app/tables.py apps/worker/app/pricing.py apps/worker/app/config.py \
  apps/worker/app/tasks/messages.py apps/worker/tests/unit/
git commit -m "feat(worker): consumo ponderado fracionado via pricing_configs, moeda única e lock de débito"
```

---

### Task 3: `api` — cálculo ponderado no resumo e nas conversas de teste

**Files:**
- Modify: `apps/api/app/services/pricing.py` (adicionar `calcular_creditos` — mesmo corpo da Task 1 Step 4, com `PricingConfig` tipado)
- Modify: `apps/api/app/api/v1/conversations.py` (rota de summary), `apps/api/app/services/test_conversations.py`
- Modify: `apps/api/app/core/config.py` (remover `credit_tokens_per_credit`)
- Test: `apps/api/tests/unit/test_pricing_service.py` (casos do cálculo), `apps/api/tests/unit/test_conversations_routes.py`, `apps/api/tests/unit/test_test_conversations_routes.py`

**Interfaces:**
- Consumes: `get_current_pricing_config` (Etapa 1), colunas de auditoria (Etapa 1).
- Produces: resumo e conversas de teste debitam `calcular_creditos(...)` (Decimal 4 casas) com `pricing_config_id` no lançamento e `FOR UPDATE` na linha do tenant antes do update relativo.

- [ ] **Step 1:** Testes do cálculo em `test_pricing_service.py` (mesmos 4 casos da Task 1 Step 2, importando de `app.services.pricing`). Rodar → falha.

- [ ] **Step 2:** Implementar `calcular_creditos` em `app/services/pricing.py` (corpo idêntico ao do worker; assinatura `(tokens_input: int, tokens_output: int, tokens_used: int, config: PricingConfig) -> Decimal`). Rodar → passa.

- [ ] **Step 3:** `conversations.py` (summary): trocar a fórmula por
```python
    config = await get_current_pricing_config(session)
    credits = calcular_creditos(
        summary_result.get("tokens_input", 0),
        summary_result.get("tokens_output", 0),
        tokens_used,
        config,
    )
```
com `pricing_config_id=config.id` no `CreditTransaction` e, antes do `update(Tenant)`, o lock:
```python
    await session.execute(
        select(Tenant.credit_balance).where(Tenant.id == ctx.tenant_id).with_for_update()
    )
```
Remover import de `math` se ficar sem uso. Mesma mudança em `test_conversations.py` (service).
Nos testes de rota, monkeypatch `conversations_module.get_current_pricing_config` / `test_conversations_module.service.get_current_pricing_config` (AsyncMock devolvendo SimpleNamespace(id=uuid4(), tokens_per_credit=1000, input_weight=Decimal("0.3"), output_weight=Decimal("1.0"))) e ajustar asserts: resumo 2500 tokens (2000 in/500 out) → `-Decimal("1.1000")`; teste 3500 (2800/700) → `-Decimal("1.5400")`.

- [ ] **Step 4:** Remover `credit_tokens_per_credit` de `app/core/config.py`.

- [ ] **Step 5:** `uv run pytest tests/unit` + `uv run ruff check .` → PASS. Commit:
```bash
git add apps/api/app/services/pricing.py apps/api/app/api/v1/conversations.py \
  apps/api/app/services/test_conversations.py apps/api/app/core/config.py \
  apps/api/tests/unit/test_pricing_service.py apps/api/tests/unit/test_conversations_routes.py \
  apps/api/tests/unit/test_test_conversations_routes.py
git commit -m "feat(api): resumo e conversas de teste com consumo ponderado fracionado"
```

---

### Task 4: `api` — migration 0014 (política de saldo insuficiente, hook)

**Files:**
- Create: `apps/api/alembic/versions/0014_insufficient_balance_policy.py`
- Modify: `apps/api/app/models/end_customer_billing.py` (`TenantBillingSettings`)

**Interfaces:**
- Produces: coluna `insufficient_balance_policy` (String, NOT NULL, default `'block_with_message'`) — hook de extensibilidade (único valor suportado; mesmo padrão do `billing_mode`). Nenhum comportamento muda.

- [ ] **Step 1:** Migration:

```python
"""politica de saldo insuficiente do cliente final (hook, default block_with_message)

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_billing_settings",
        sa.Column(
            "insufficient_balance_policy",
            sa.String(),
            server_default=sa.text("'block_with_message'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_billing_settings", "insufficient_balance_policy")
```

- [ ] **Step 2:** Model (`TenantBillingSettings`), após `billing_mode`:
```python
    # Único valor suportado por ora — hook de extensibilidade (como billing_mode).
    insufficient_balance_policy: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'block_with_message'")
    )
```

- [ ] **Step 3:** `alembic upgrade head` + `downgrade 0013` + `upgrade head` no container → sem erro. Suíte verde. Commit:
```bash
git add apps/api/alembic/versions/0014_insufficient_balance_policy.py apps/api/app/models/end_customer_billing.py
git commit -m "feat(api): insufficient_balance_policy em tenant_billing_settings (hook, sem mudança de comportamento)"
```

---

### Task 5: schemas float + formatação no web

**Files:**
- Modify: `apps/api/app/schemas/billing.py:7`, `apps/api/app/schemas/dashboard.py:19,37`, `apps/api/app/schemas/admin_dashboard.py:19-20,26`, `apps/api/app/schemas/admin_tenants.py:11,21,40` — campos de créditos `int` → `float`
- Create: `apps/web/src/lib/format.ts` (`formatCredits`)
- Modify: `apps/web/src/components/{DashboardPanel,CreditosPanel,LowBalanceBanner?,AdminDashboardPanel,AdminTenantsList,AdminTenantDetail}.tsx` (exibição via `formatCredits`)
- Test: testes do web que renderizam saldo (ajustar expectativas se houver), `pnpm test && pnpm lint && pnpm build`

**Interfaces:**
- Produces: `formatCredits(n: number): string` → `n.toLocaleString("pt-BR", { maximumFractionDigits: 2 })`. Front continua tipando `number` (JSON number).

- [ ] **Step 1:** Schemas: trocar os `int` de créditos por `float` (só os campos de créditos — contagens como `agent_messages`, `total`, `tokens_consumed` continuam `int`).
- [ ] **Step 2:** `apps/web/src/lib/format.ts`:
```ts
export function formatCredits(value: number): string {
  return value.toLocaleString("pt-BR", { maximumFractionDigits: 2 });
}
```
- [ ] **Step 3:** Usar nos componentes: `DashboardPanel` (saldo + consumo 30d), `CreditosPanel` (saldo), `AdminDashboardPanel` (lista de menor saldo + créditos vendidos/consumidos se exibidos), `AdminTenantsList` (coluna saldo), `AdminTenantDetail` (saldo + amount_credits das transações). `LowBalanceBanner` só compara `<= 0` — sem mudança.
- [ ] **Step 4:** `cd apps/web && pnpm test && pnpm lint && pnpm build` → verde (ajustar testes que esperem o número cru).
- [ ] **Step 5:** `cd apps/api && uv run pytest tests/unit` → verde. Commit:
```bash
git add apps/api/app/schemas/ apps/web/src/
git commit -m "feat: créditos fracionados na API (float) e formatação pt-BR no painel"
```

---

### Task 6: Verificação final + CLAUDE.md

- [ ] Suítes: agents (não tocado — sanidade), api, worker, web → verdes.
- [ ] `alembic upgrade head` idempotente.
- [ ] Atualizar CLAUDE.md (seção Billing / Créditos): regra de consumo agora ponderada/fracionada via `pricing_configs`, moeda única (débito do cliente final substitui o do tenant quando custeado pelo cliente), gate do worker refinado, `end_customer_tokens_per_credit` deprecado, envs `CREDIT_TOKENS_PER_CREDIT` removidas, política `insufficient_balance_policy` (hook).
- [ ] Commit `docs: registra consumo ponderado e moeda única (etapa 2) no CLAUDE.md`.
