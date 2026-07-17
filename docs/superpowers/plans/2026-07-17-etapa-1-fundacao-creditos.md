# Etapa 1 — Fundação da Wallet de Créditos Unificada — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preparar o schema e os contratos internos para o modelo de moeda única de créditos (tokens ponderados, créditos fracionados, ledger com resale/adjustment e auditoria de tokens), **sem nenhuma mudança de comportamento visível** — a fórmula de débito (`ceil(tokens/1000)` via env) permanece intacta até a Etapa 2.

**Architecture:** O `agents` passa a devolver tokens de input/output separados (além do total); o `api` ganha a tabela global versionada `pricing_configs` (seedada com 1000 tokens/crédito, pesos 0.3/1.0) e migra os saldos/ledgers de Integer para Numeric(12,4); os dois ledgers ganham os tipos `resale`/`adjustment` e colunas de auditoria (`tokens_input`, `tokens_output`, `pricing_config_id`); `worker` e `api` começam a gravar os tokens brutos em cada lançamento de consumo (auditoria pura, sem mudar valores).

**Tech Stack:** FastAPI + SQLAlchemy 2 (async) + Alembic (api), Arq + SQLAlchemy Core (worker), FastAPI + LangGraph (agents), pytest + pytest-asyncio.

## Global Constraints

- Proporção base: **1 crédito = 1000 tokens ponderados** — vive na `pricing_configs` (seed), nunca hardcoded em código de negócio.
- Pesos iniciais: **INPUT_WEIGHT = 0.3**, **OUTPUT_WEIGHT = 1.0** — idem, só na seed da `pricing_configs`.
- Etapa 1 **não altera nenhum valor debitado** — a fórmula `math.ceil(tokens_used / settings.credit_tokens_per_credit)` e a env `CREDIT_TOKENS_PER_CREDIT` permanecem como estão (a troca pela ponderação é a Etapa 2).
- Precisão fracionada: **Numeric(12, 4)** em todos os saldos e `amount_credits`.
- Campo novo em resposta de API interna é **aditivo** (nunca remover `tokens_used` — os chamadores antigos continuam funcionando durante o deploy).
- Commits: Conventional Commits em pt-BR (`feat(agents):`, `feat(api):`, `feat(worker):`), mensagens curtas no padrão do repo (ver `git log`).
- Lint/testes por app: `uv run ruff check .` + `uv run pytest tests/unit` dentro de `apps/api` e `apps/worker`; `uv run pytest tests/unit` dentro de `apps/agents` (não rodar `tests/integration` do agents — exige LLM real). Docs `apps/agents/API_AGENTS.md` devem ser atualizadas junto com o código do agents (regra do CLAUDE.md).
- ⚠️ A árvore de trabalho tem mudanças pré-existentes só de formatação em `apps/api/app/api/v1/end_customer_billing.py`, `apps/api/app/models/end_customer_billing.py`, `apps/api/tests/unit/test_end_customer_billing_service.py` e `.claude/settings.local.json`. **Não incluí-las nos commits das tasks** — `git add` sempre com paths explícitos.

---

### Task 1: `agents` — breakdown de tokens (input/output/total) nos services

**Files:**
- Modify: `apps/agents/services/call_agent.py:27-38` (função `sum_usage_tokens`), `apps/agents/services/call_agent.py:85-105` (retorno de `run_agent`)
- Modify: `apps/agents/services/summarize.py:10,29-36`
- Test: `apps/agents/tests/unit/test_call_agent.py`, `apps/agents/tests/unit/test_summarize.py`

**Interfaces:**
- Consumes: `usage_metadata` do langchain-openai (dict com `input_tokens`, `output_tokens`, `total_tokens` em cada `AIMessage`).
- Produces: `sum_usage_breakdown(messages: list) -> dict` com chaves `input_tokens`/`output_tokens`/`total_tokens` (ints); `run_agent(...) -> tuple[list[str], dict, str]` (o dict é o breakdown); `summarize_conversation(messages) -> tuple[str, dict]`. **A função `sum_usage_tokens` deixa de existir** — Task 2 usa esses novos retornos.

- [ ] **Step 1: Reescrever os testes de `sum_usage_breakdown`** — substituir o conteúdo de `apps/agents/tests/unit/test_call_agent.py` por:

```python
from types import SimpleNamespace

from services.call_agent import sum_usage_breakdown


def _ai(usage: dict | None):
    return SimpleNamespace(type="ai", usage_metadata=usage, content="resposta")


def _human():
    return SimpleNamespace(type="human", content="pergunta")


def test_soma_input_output_e_total_das_mensagens_de_ia():
    messages = [
        _human(),
        _ai({"input_tokens": 70, "output_tokens": 30, "total_tokens": 100}),
        _ai({"input_tokens": 200, "output_tokens": 50, "total_tokens": 250}),
    ]

    assert sum_usage_breakdown(messages) == {
        "input_tokens": 270,
        "output_tokens": 80,
        "total_tokens": 350,
    }


def test_mensagem_de_ia_sem_usage_conta_zero():
    messages = [_ai(None), _ai({"input_tokens": 60, "output_tokens": 20, "total_tokens": 80})]

    assert sum_usage_breakdown(messages) == {
        "input_tokens": 60,
        "output_tokens": 20,
        "total_tokens": 80,
    }


def test_mensagem_sem_atributo_usage_metadata_nao_quebra():
    messages = [SimpleNamespace(type="ai", content="x"), _human()]

    assert sum_usage_breakdown(messages) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


def test_usage_sem_chaves_de_input_output_usa_zero():
    messages = [_ai({"total_tokens": 40})]

    assert sum_usage_breakdown(messages) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 40,
    }
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/agents && uv run pytest tests/unit/test_call_agent.py -v`
Expected: FAIL — `ImportError: cannot import name 'sum_usage_breakdown'`

- [ ] **Step 3: Implementar `sum_usage_breakdown` e atualizar `run_agent`** — em `apps/agents/services/call_agent.py`, substituir a função `sum_usage_tokens` (linhas 27-38) por:

```python
def sum_usage_breakdown(messages: list) -> dict:
    """Soma os tokens (input/output/total) das mensagens de IA de uma execução.

    O usage_metadata é preenchido pelo langchain-openai em cada AIMessage —
    inclui as chamadas intermediárias com tool_calls, que também custam tokens.
    Input e output separados alimentam a ponderação de créditos no chamador.
    """
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for m in messages:
        usage = getattr(m, "usage_metadata", None)
        if m.type == "ai" and usage:
            totals["input_tokens"] += usage.get("input_tokens", 0)
            totals["output_tokens"] += usage.get("output_tokens", 0)
            totals["total_tokens"] += usage.get("total_tokens", 0)
    return totals
```

E no corpo de `run_agent` (linhas 85-105 atuais), trocar:

```python
    tokens_used = sum_usage_tokens(new_messages)
```
por:
```python
    usage = sum_usage_breakdown(new_messages)
```
trocar o log (linha ~96) `tokens_used,` por `usage["total_tokens"],` e o retorno final por:
```python
    return answers, usage, current_agent
```
Ajustar também a anotação de retorno da assinatura de `run_agent` de `-> tuple[list[str], int, str]` para `-> tuple[list[str], dict, str]`.

- [ ] **Step 4: Atualizar `summarize.py`** — trocar o import da linha 10 para `from services.call_agent import langfuse_handler, sum_usage_breakdown` e o corpo de `summarize_conversation` para:

```python
async def summarize_conversation(messages: list[dict]) -> tuple[str, dict]:
    transcript = _format_transcript(messages)
    response = await model.ainvoke(
        [SystemMessage(content=SUMMARY_PROMPT), HumanMessage(content=transcript)],
        config={"callbacks": [langfuse_handler]},
    )
    usage = sum_usage_breakdown([response])
    return response.content, usage
```

- [ ] **Step 5: Atualizar `test_summarize.py`** — nos 3 testes, o segundo elemento do retorno vira dict:
  - `test_gera_resumo_e_soma_os_tokens`: renomear a variável `tokens_used` para `usage` e trocar `assert tokens_used == 15` por `assert usage == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}`.
  - `test_sem_usage_metadata_retorna_zero_tokens`: trocar `_, tokens_used = ...` / `assert tokens_used == 0` por `_, usage = ...` / `assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}`.
  - `test_monta_a_transcricao_com_rotulos_em_portugues`: sem mudança (não usa o retorno).

- [ ] **Step 6: Rodar os dois arquivos de teste**

Run: `cd apps/agents && uv run pytest tests/unit/test_call_agent.py tests/unit/test_summarize.py -v`
Expected: PASS (7 testes)

- [ ] **Step 7: Rodar a suíte unit inteira do agents** (as rotas ainda usam o retorno antigo — vão falhar; é esperado e a Task 2 conserta; se preferir suite verde por commit, fazer Task 1+2 num commit só — escolha registrada aqui: **commit único ao fim da Task 2**)

Run: `cd apps/agents && uv run pytest tests/unit -v`
Expected: `test_routes.py` FALHA em `TypeError`/asserts do retorno de `run_agent` (3-tupla com int). Não commitar ainda.

---

### Task 2: `agents` — contrato das rotas expõe `tokens_input`/`tokens_output` + docs

**Files:**
- Modify: `apps/agents/api/routes.py:120-166` (rota `/messages`), `apps/agents/api/routes.py:212-234` (rota `/summaries`)
- Modify: `apps/agents/API_AGENTS.md` (seções dos contratos de `POST /messages` e `POST /summaries`)
- Test: `apps/agents/tests/unit/test_routes.py`

**Interfaces:**
- Consumes: `run_agent(...) -> (answers, usage: dict, current_agent)` e `summarize_conversation(...) -> (summary, usage: dict)` da Task 1.
- Produces: resposta de `POST /messages` = `{"responses", "tokens_used", "tokens_input", "tokens_output", "current_agent", "delivery_failures"}`; resposta de `POST /summaries` = `{"summary", "tokens_used", "tokens_input", "tokens_output"}`. `tokens_used` continua sendo o total (compat com chamadores antigos).

- [ ] **Step 1: Atualizar os testes de rota** — em `apps/agents/tests/unit/test_routes.py`, todo mock de `run_agent` no formato `AsyncMock(return_value=(respostas, 1234, "agente"))` vira `AsyncMock(return_value=(respostas, {"input_tokens": 1000, "output_tokens": 234, "total_tokens": 1234}, "agente"))`. No `test_fluxo_feliz_envia_respostas_e_retorna_lista`, o JSON esperado vira:

```python
    assert response.json() == {
        "responses": ["resposta 1", "resposta 2"],
        "tokens_used": 1234,
        "tokens_input": 1000,
        "tokens_output": 234,
        "current_agent": "agente_secretaria",
        "delivery_failures": [],
    }
```

Aplicar a mesma transformação em **todos** os demais testes do arquivo que mockam `run_agent` ou assertam o JSON de `/messages`, e nos testes de `/summaries` que mockam `summarize_conversation` (retorno vira `("resumo", {"input_tokens": X, "output_tokens": Y, "total_tokens": Z})` e o JSON esperado ganha `tokens_input`/`tokens_output`).

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/agents && uv run pytest tests/unit/test_routes.py -v`
Expected: FAIL — respostas sem `tokens_input`/`tokens_output`

- [ ] **Step 3: Atualizar as rotas** — em `apps/agents/api/routes.py`:

Na rota `/messages` (linha ~122), trocar:
```python
        response, tokens_used, current_agent = await run_agent(
```
por:
```python
        response, usage, current_agent = await run_agent(
```
e o retorno (linhas ~161-166) por:
```python
        return {
            "responses": response,
            "tokens_used": usage["total_tokens"],
            "tokens_input": usage["input_tokens"],
            "tokens_output": usage["output_tokens"],
            "current_agent": current_agent,
            "delivery_failures": delivery_failures,
        }
```

Na rota `/summaries` (linha ~221), trocar `summary, tokens_used = await summarize_conversation(` por `summary, usage = await summarize_conversation(` e o retorno por:
```python
    return {
        "summary": summary,
        "tokens_used": usage["total_tokens"],
        "tokens_input": usage["input_tokens"],
        "tokens_output": usage["output_tokens"],
    }
```

- [ ] **Step 4: Rodar a suíte unit inteira + lint**

Run: `cd apps/agents && uv run pytest tests/unit -v`
Expected: PASS (todos)

- [ ] **Step 5: Atualizar `API_AGENTS.md`** — nas seções que documentam a resposta de `POST /messages` e `POST /summaries`, acrescentar os campos novos com uma linha de explicação, ex.:

```
- `tokens_used` (int): total de tokens da execução (input + output).
- `tokens_input` (int): tokens de prompt/input da execução.
- `tokens_output` (int): tokens de completion/output da execução.
```

- [ ] **Step 6: Commit (Tasks 1+2 juntas — suíte verde)**

```bash
git add apps/agents/services/call_agent.py apps/agents/services/summarize.py \
  apps/agents/api/routes.py apps/agents/API_AGENTS.md \
  apps/agents/tests/unit/test_call_agent.py apps/agents/tests/unit/test_summarize.py \
  apps/agents/tests/unit/test_routes.py
git commit -m "feat(agents): tokens de input/output separados em /messages e /summaries"
```

---

### Task 3: `api` — models + migration 0013 (pricing_configs, Numeric, resale/adjustment, auditoria)

**Files:**
- Modify: `apps/api/app/models/billing.py` (novo model `PricingConfig`; `CreditTransaction` fracionado + colunas de auditoria)
- Modify: `apps/api/app/models/end_customer_billing.py` (`EndCustomerCreditTransaction` idem; `EndCustomerBalance.credit_balance` → Numeric)
- Modify: `apps/api/app/models/tenant.py:25` (`credit_balance` → Numeric)
- Modify: `apps/api/app/models/message.py:51` (`credits_consumed` → Numeric(12, 4))
- Modify: `apps/api/app/models/__init__.py` (exportar `PricingConfig`)
- Create: `apps/api/alembic/versions/0013_fundacao_wallet_creditos.py`

**Interfaces:**
- Produces: model `PricingConfig` (`tokens_per_credit: int`, `input_weight: Decimal`, `output_weight: Decimal`, `effective_at: datetime`) importável de `app.models`; colunas `tokens_input`/`tokens_output`/`pricing_config_id` nos dois ledgers; tipos `resale`/`adjustment` válidos nos checks. Tasks 4-6 dependem disso.
- Nota de compat: schemas Pydantic que expõem `credit_balance` como `int` continuam funcionando — Pydantic v2 coage `Decimal` integral para `int` sem perda, e os valores permanecem inteiros até a Etapa 2 (quando os schemas migram junto com a ponderação).

- [ ] **Step 1: Adicionar `PricingConfig` em `apps/api/app/models/billing.py`** (após os imports, antes de `CreditPackage`):

```python
class PricingConfig(Base):
    """Config global de pricing, versionada (não tenant-scoped).

    Nunca editar uma linha existente: mudança de pesos/proporção cria uma
    linha nova com `effective_at`; cada lançamento de consumo grava a config
    vigente no momento (auditoria/recalculabilidade do histórico).
    """

    __tablename__ = "pricing_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tokens_per_credit: Mapped[int] = mapped_column(Integer, nullable=False)
    input_weight: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    output_weight: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
```

- [ ] **Step 2: Atualizar `CreditTransaction`** no mesmo arquivo — trocar o check e `amount_credits`, e adicionar as colunas de auditoria após `amount_credits`:

```python
    __table_args__ = (
        CheckConstraint(
            "type IN ('purchase', 'consumption', 'refund', 'bonus', 'resale', 'adjustment')",
            name="type",
        ),
    )
```
```python
    # Positivo em purchase/bonus, negativo em consumption/resale (saída do estoque).
    amount_credits: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # Auditoria de consumo: tokens brutos + config de pricing vigente no débito.
    tokens_input: Mapped[int | None] = mapped_column(Integer)
    tokens_output: Mapped[int | None] = mapped_column(Integer)
    pricing_config_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pricing_configs.id")
    )
```

- [ ] **Step 3: Atualizar `apps/api/app/models/end_customer_billing.py`** — em `EndCustomerCreditTransaction`, o check vira `"type IN ('purchase', 'consumption', 'resale', 'adjustment')"`, `amount_credits` vira `Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)` e ganham-se as mesmas 3 colunas de auditoria do Step 2. Em `EndCustomerBalance`, `credit_balance` vira:

```python
    credit_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, server_default=text("0")
    )
```
(Adicionar `from decimal import Decimal` já existe no arquivo.)

- [ ] **Step 4: Atualizar `tenant.py` e `message.py`** — em `apps/api/app/models/tenant.py:25`:

```python
    credit_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, server_default=text("0")
    )
```
(adicionar `from decimal import Decimal` e `Numeric` ao import de sqlalchemy). Em `apps/api/app/models/message.py:51`, trocar `Numeric(12, 2)` por `Numeric(12, 4)`.

- [ ] **Step 5: Exportar `PricingConfig`** — em `apps/api/app/models/__init__.py`, adicionar `PricingConfig` ao import de `app.models.billing` e ao `__all__` (se houver).

- [ ] **Step 6: Criar a migration `apps/api/alembic/versions/0013_fundacao_wallet_creditos.py`**:

```python
"""fundação da wallet unificada: pricing_configs, saldos fracionados,
tipos resale/adjustment e auditoria de tokens nos ledgers

Etapa 1 do modelo de moeda única — nenhuma mudança de comportamento:
a conversão tokens->créditos continua na env até a Etapa 2.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-17
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

LEDGERS = ["credit_transactions", "end_customer_credit_transactions"]
# Curto de propósito: o nome por convenção estoura os 63 chars do Postgres.
FK_END_CUSTOMER_PRICING = "fk_end_customer_credit_transactions_pricing_config_id"


def upgrade() -> None:
    # Config global de pricing, versionada. Não tenant-scoped (como
    # credit_packages) — sem RLS; grants vêm dos DEFAULT PRIVILEGES da 0008.
    op.create_table(
        "pricing_configs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tokens_per_credit", sa.Integer(), nullable=False),
        sa.Column("input_weight", sa.Numeric(6, 4), nullable=False),
        sa.Column("output_weight", sa.Numeric(6, 4), nullable=False),
        sa.Column(
            "effective_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pricing_configs")),
    )
    # Config inicial: 1 crédito = 1000 tokens ponderados, pesos 0.3/1.0.
    op.execute(
        "INSERT INTO pricing_configs (tokens_per_credit, input_weight, output_weight) "
        "VALUES (1000, 0.3, 1.0)"
    )

    # Créditos fracionados (4 casas) em saldos e ledgers. int->numeric é lossless.
    op.alter_column(
        "tenants",
        "credit_balance",
        type_=sa.Numeric(12, 4),
        existing_type=sa.Integer(),
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )
    op.alter_column(
        "end_customer_balances",
        "credit_balance",
        type_=sa.Numeric(12, 4),
        existing_type=sa.Integer(),
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )
    op.alter_column(
        "credit_transactions",
        "amount_credits",
        type_=sa.Numeric(12, 4),
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.alter_column(
        "end_customer_credit_transactions",
        "amount_credits",
        type_=sa.Numeric(12, 4),
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.alter_column(
        "messages",
        "credits_consumed",
        type_=sa.Numeric(12, 4),
        existing_type=sa.Numeric(12, 2),
        existing_nullable=True,
    )

    # Tipos novos de lançamento nos dois ledgers (mesmo padrão da 0009 com
    # sender_type: o nome curto expande pela naming convention).
    op.drop_constraint("type", "credit_transactions", type_="check")
    op.create_check_constraint(
        "type",
        "credit_transactions",
        "type IN ('purchase', 'consumption', 'refund', 'bonus', 'resale', 'adjustment')",
    )
    op.drop_constraint("type", "end_customer_credit_transactions", type_="check")
    op.create_check_constraint(
        "type",
        "end_customer_credit_transactions",
        "type IN ('purchase', 'consumption', 'resale', 'adjustment')",
    )

    # Auditoria de consumo: tokens brutos + config vigente no momento do débito.
    for table in LEDGERS:
        op.add_column(table, sa.Column("tokens_input", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("tokens_output", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("pricing_config_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_credit_transactions_pricing_config_id_pricing_configs"),
        "credit_transactions",
        "pricing_configs",
        ["pricing_config_id"],
        ["id"],
    )
    op.create_foreign_key(
        FK_END_CUSTOMER_PRICING,
        "end_customer_credit_transactions",
        "pricing_configs",
        ["pricing_config_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        FK_END_CUSTOMER_PRICING, "end_customer_credit_transactions", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_credit_transactions_pricing_config_id_pricing_configs"),
        "credit_transactions",
        type_="foreignkey",
    )
    for table in LEDGERS:
        op.drop_column(table, "pricing_config_id")
        op.drop_column(table, "tokens_output")
        op.drop_column(table, "tokens_input")

    op.drop_constraint("type", "end_customer_credit_transactions", type_="check")
    op.create_check_constraint(
        "type", "end_customer_credit_transactions", "type IN ('purchase', 'consumption')"
    )
    op.drop_constraint("type", "credit_transactions", type_="check")
    op.create_check_constraint(
        "type",
        "credit_transactions",
        "type IN ('purchase', 'consumption', 'refund', 'bonus')",
    )

    op.alter_column(
        "messages",
        "credits_consumed",
        type_=sa.Numeric(12, 2),
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "end_customer_credit_transactions",
        "amount_credits",
        type_=sa.Integer(),
        existing_type=sa.Numeric(12, 4),
        postgresql_using="amount_credits::integer",
        existing_nullable=False,
    )
    op.alter_column(
        "credit_transactions",
        "amount_credits",
        type_=sa.Integer(),
        existing_type=sa.Numeric(12, 4),
        postgresql_using="amount_credits::integer",
        existing_nullable=False,
    )
    op.alter_column(
        "end_customer_balances",
        "credit_balance",
        type_=sa.Integer(),
        existing_type=sa.Numeric(12, 4),
        postgresql_using="credit_balance::integer",
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )
    op.alter_column(
        "tenants",
        "credit_balance",
        type_=sa.Integer(),
        existing_type=sa.Numeric(12, 4),
        postgresql_using="credit_balance::integer",
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )
    op.drop_table("pricing_configs")
```

- [ ] **Step 7: Rodar a migration contra o Postgres local**

Run: `docker compose up -d postgres && cd apps/api && uv run alembic upgrade head`
Expected: `Running upgrade 0012 -> 0013, fundação da wallet unificada...` sem erro.

- [ ] **Step 8: Testar o downgrade/upgrade (reversibilidade)**

Run: `cd apps/api && uv run alembic downgrade 0012 && uv run alembic upgrade head`
Expected: ambos sem erro.

- [ ] **Step 9: Rodar suíte + lint do api**

Run: `cd apps/api && uv run ruff check . && uv run pytest tests/unit`
Expected: PASS (asserts de valores int continuam válidos — `Decimal(4) == 4` é True em Python).

- [ ] **Step 10: Commit**

```bash
git add apps/api/app/models/billing.py apps/api/app/models/tenant.py \
  apps/api/app/models/message.py apps/api/app/models/__init__.py \
  apps/api/alembic/versions/0013_fundacao_wallet_creditos.py
git add -p apps/api/app/models/end_customer_billing.py  # só os hunks desta task (o arquivo tem formatação pré-existente não commitada)
git commit -m "feat(api): pricing_configs versionada, créditos fracionados e auditoria de tokens nos ledgers"
```

---

### Task 4: `api` — serviço de leitura da pricing config vigente

**Files:**
- Create: `apps/api/app/services/pricing.py`
- Test: `apps/api/tests/unit/test_pricing_service.py`

**Interfaces:**
- Consumes: model `PricingConfig` (Task 3).
- Produces: `async def get_current_pricing_config(session: AsyncSession) -> PricingConfig` — consumida na Etapa 2 pelos pontos de débito do `api` (resumo, conversas de teste). Levanta `RuntimeError` se não houver config (erro de deploy — a 0013 seeda a inicial).

- [ ] **Step 1: Escrever o teste** — `apps/api/tests/unit/test_pricing_service.py`:

```python
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.pricing import get_current_pricing_config


async def test_retorna_a_config_vigente():
    config = SimpleNamespace(
        tokens_per_credit=1000, input_weight=Decimal("0.3"), output_weight=Decimal("1.0")
    )
    session = AsyncMock()
    session.scalar.return_value = config

    result = await get_current_pricing_config(session)

    assert result is config
    session.scalar.assert_awaited_once()


async def test_sem_config_levanta_runtime_error():
    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(RuntimeError, match="pricing_config"):
        await get_current_pricing_config(session)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_pricing_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.pricing'`

- [ ] **Step 3: Implementar** — `apps/api/app/services/pricing.py`:

```python
"""Leitura da config global de pricing (pesos input/output, tokens por crédito).

A tabela é versionada: a config vigente é a de `effective_at` mais recente já
alcançado. A migration 0013 seeda a inicial — ausência de config é erro de
deploy, não estado válido.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PricingConfig


async def get_current_pricing_config(session: AsyncSession) -> PricingConfig:
    config = await session.scalar(
        select(PricingConfig)
        .where(PricingConfig.effective_at <= datetime.now(UTC))
        .order_by(PricingConfig.effective_at.desc())
        .limit(1)
    )
    if config is None:
        raise RuntimeError(
            "Nenhuma pricing_config vigente — rode as migrations (0013 seeda a inicial)"
        )
    return config
```

- [ ] **Step 4: Rodar e ver passar + lint**

Run: `cd apps/api && uv run pytest tests/unit/test_pricing_service.py -v && uv run ruff check .`
Expected: PASS (2 testes)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/pricing.py apps/api/tests/unit/test_pricing_service.py
git commit -m "feat(api): leitura da pricing config vigente (get_current_pricing_config)"
```

---

### Task 5: `worker` — sync do schema + tokens brutos nos lançamentos de consumo

**Files:**
- Modify: `apps/worker/app/tables.py` (tipos Numeric + colunas de auditoria)
- Modify: `apps/worker/app/clients/agents.py:44-48` (retorno com breakdown)
- Modify: `apps/worker/app/tasks/messages.py:183-215,376-429` (propagação + inserts)
- Test: `apps/worker/tests/unit/test_agents_client.py`, `apps/worker/tests/unit/test_debitar_creditos_cliente_final.py`, `apps/worker/tests/unit/test_process_inbound_message.py`

**Interfaces:**
- Consumes: campos `tokens_input`/`tokens_output` da resposta de `POST /messages` (Task 2).
- Produces: `send_message_to_agents(...)` retorna dict com chaves adicionais `tokens_input: int` e `tokens_output: int`; `_debitar_creditos(session, tenant_id, message_id, tokens_used, credits, tokens_input=0, tokens_output=0)` e `_debitar_creditos_cliente_final(session, tenant_id, contact_phone_number, message_id, tokens_used, credits, tokens_input=0, tokens_output=0)` gravam os tokens brutos no lançamento (NULL quando zero; `pricing_config_id` fica NULL até a Etapa 2, quando a conversão passar a usar a config).

- [ ] **Step 1: Atualizar `tables.py`** — em `apps/worker/app/tables.py`:
  - `tenants`: `Column("credit_balance", Numeric(12, 4))`
  - `messages`: `Column("credits_consumed", Numeric(12, 4))`
  - `credit_transactions`: `Column("amount_credits", Numeric(12, 4))` + adicionar `Column("tokens_input", Integer)`, `Column("tokens_output", Integer)`, `Column("pricing_config_id", Uuid)` após `amount_credits`
  - `end_customer_balances`: `Column("credit_balance", Numeric(12, 4))`
  - `end_customer_credit_transactions`: `Column("amount_credits", Numeric(12, 4))` + as mesmas 3 colunas de auditoria

- [ ] **Step 2: Teste do client** — em `apps/worker/tests/unit/test_agents_client.py`, no teste do caminho 200 (resposta com corpo), acrescentar ao JSON mockado do agents `"tokens_input": 1000, "tokens_output": 234` e assertar que o retorno de `send_message_to_agents` contém `"tokens_input": 1000` e `"tokens_output": 234`. Acrescentar também um teste de compat:

```python
async def test_resposta_sem_breakdown_de_tokens_usa_zero(...):
    # JSON do agents sem tokens_input/tokens_output (versão antiga no deploy)
    ...
    assert result["tokens_input"] == 0
    assert result["tokens_output"] == 0
```
(Seguir o padrão de mock HTTP já usado no arquivo.)

- [ ] **Step 3: Rodar e ver falhar**

Run: `cd apps/worker && uv run pytest tests/unit/test_agents_client.py -v`
Expected: FAIL — `KeyError: 'tokens_input'`

- [ ] **Step 4: Atualizar o client** — em `apps/worker/app/clients/agents.py`, o retorno vira:

```python
    return {
        "responses": data.get("responses", []),
        "tokens_used": data.get("tokens_used", 0),
        "tokens_input": data.get("tokens_input", 0),
        "tokens_output": data.get("tokens_output", 0),
        "delivery_failures": data.get("delivery_failures", []),
    }
```
(Atualizar também o docstring da função.)

- [ ] **Step 5: Teste dos débitos** — em `apps/worker/tests/unit/test_debitar_creditos_cliente_final.py`, atualizar a chamada e asserts:

```python
    await messages_task._debitar_creditos_cliente_final(
        session,
        TENANT_ID,
        CONTACT,
        MESSAGE_ID,
        tokens_used=2000,
        credits=4,
        tokens_input=1400,
        tokens_output=600,
    )

    transaction = session.executed[0]
    assert transaction["type"] == "consumption"
    assert transaction["amount_credits"] == -4
    assert transaction["contact_phone_number"] == CONTACT
    assert transaction["related_message_id"] == MESSAGE_ID
    assert transaction["tokens_input"] == 1400
    assert transaction["tokens_output"] == 600
```

E adicionar teste equivalente para `_debitar_creditos` (tenant) no mesmo padrão `FakeSession`, incluindo o caso `tokens_input=0` → gravado como `None`:

```python
async def test_debito_do_tenant_grava_tokens_brutos() -> None:
    session = FakeSession()

    await messages_task._debitar_creditos(
        session, TENANT_ID, MESSAGE_ID, tokens_used=2000, credits=2,
        tokens_input=1400, tokens_output=600,
    )

    transaction = session.executed[0]
    assert transaction["type"] == "consumption"
    assert transaction["amount_credits"] == -2
    assert transaction["tokens_input"] == 1400
    assert transaction["tokens_output"] == 600


async def test_sem_breakdown_grava_null_em_tokens() -> None:
    session = FakeSession()

    await messages_task._debitar_creditos(
        session, TENANT_ID, MESSAGE_ID, tokens_used=2000, credits=2
    )

    transaction = session.executed[0]
    assert transaction["tokens_input"] is None
    assert transaction["tokens_output"] is None
```

- [ ] **Step 6: Rodar e ver falhar**

Run: `cd apps/worker && uv run pytest tests/unit/test_debitar_creditos_cliente_final.py -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'tokens_input'`

- [ ] **Step 7: Implementar em `messages.py`** — em `process_inbound_message` (após linha 184):

```python
    tokens_input = result.get("tokens_input", 0)
    tokens_output = result.get("tokens_output", 0)
```
passar nos dois débitos: `_debitar_creditos(session, tenant_id, first_message_id, tokens_used, credits, tokens_input, tokens_output)` e `_debitar_creditos_cliente_final(..., end_customer_credits, tokens_input, tokens_output)`. As funções ganham os parâmetros e gravam no insert:

```python
async def _debitar_creditos(
    session: AsyncSession,
    tenant_id: str,
    message_id: uuid.UUID,
    tokens_used: int,
    credits: int,
    tokens_input: int = 0,
    tokens_output: int = 0,
) -> None:
    """Lança o consumo no ledger e atualiza o cache de saldo do tenant.

    tokens_input/tokens_output são auditoria pura por ora — a conversão em
    créditos continua pelo total (Etapa 2 introduz a ponderação)."""
    await session.execute(
        insert(tables.credit_transactions).values(
            tenant_id=uuid.UUID(tenant_id),
            type="consumption",
            amount_credits=-credits,
            related_message_id=message_id,
            tokens_input=tokens_input or None,
            tokens_output=tokens_output or None,
            description=f"Consumo do agente ({tokens_used} tokens)",
            created_at=datetime.now(UTC),
        )
    )
    await session.execute(
        update(tables.tenants)
        .where(tables.tenants.c.id == uuid.UUID(tenant_id))
        .values(credit_balance=tables.tenants.c.credit_balance - credits)
    )
```
(mesma mudança em `_debitar_creditos_cliente_final`, acrescentando `tokens_input=tokens_input or None, tokens_output=tokens_output or None` ao insert).

- [ ] **Step 8: Rodar a suíte unit inteira + lint**

Run: `cd apps/worker && uv run pytest tests/unit -v && uv run ruff check .`
Expected: PASS (se `test_process_inbound_message.py` mockar o retorno do client sem as chaves novas, os `.get(..., 0)` cobrem — se algum assert quebrar, atualizar o mock para incluir `tokens_input`/`tokens_output`).

- [ ] **Step 9: Commit**

```bash
git add apps/worker/app/tables.py apps/worker/app/clients/agents.py \
  apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_agents_client.py \
  apps/worker/tests/unit/test_debitar_creditos_cliente_final.py \
  apps/worker/tests/unit/test_process_inbound_message.py
git commit -m "feat(worker): tokens brutos (input/output) auditados nos lançamentos de consumo"
```

---

### Task 6: `api` — client do agents + resumo + conversas de teste gravam tokens brutos

**Files:**
- Modify: `apps/api/app/clients/agents.py:64-69,108-111` (breakdown no retorno)
- Modify: `apps/api/app/api/v1/conversations.py:211-231` (lançamento do resumo)
- Modify: `apps/api/app/services/test_conversations.py:54-90` (lançamento da conversa de teste)
- Test: `apps/api/tests/unit/test_agents_client.py`, `apps/api/tests/unit/test_conversations_routes.py`, `apps/api/tests/unit/test_test_conversations_routes.py`

**Interfaces:**
- Consumes: campos `tokens_input`/`tokens_output` das respostas do agents (Task 2); colunas de auditoria de `CreditTransaction` (Task 3).
- Produces: `send_playground_message(...)` e `generate_conversation_summary(...)` retornam dicts com `tokens_input`/`tokens_output` adicionais (default 0 quando ausentes); os `CreditTransaction` de consumo do resumo e da conversa de teste gravam `tokens_input`/`tokens_output`.

- [ ] **Step 1: Testes do client** — em `apps/api/tests/unit/test_agents_client.py`, nos testes de caminho feliz de `send_playground_message` e `generate_conversation_summary`, acrescentar `"tokens_input"`/`"tokens_output"` ao JSON mockado e assertar a presença no retorno; adicionar um caso sem as chaves → retorno com `0` (mesmo padrão da Task 5 Step 2, seguindo os mocks já usados no arquivo).

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_client.py -v`
Expected: FAIL — `KeyError: 'tokens_input'`

- [ ] **Step 3: Atualizar o client** — em `apps/api/app/clients/agents.py`:

`send_playground_message` retorna:
```python
    return {
        "responses": data.get("responses", []),
        "tokens_used": data.get("tokens_used"),
        "tokens_input": data.get("tokens_input", 0),
        "tokens_output": data.get("tokens_output", 0),
        "current_agent": data.get("current_agent"),
    }
```
`generate_conversation_summary` retorna:
```python
    return {
        "summary": data["summary"],
        "tokens_used": data.get("tokens_used", 0),
        "tokens_input": data.get("tokens_input", 0),
        "tokens_output": data.get("tokens_output", 0),
    }
```

- [ ] **Step 4: Gravar tokens brutos no resumo** — em `apps/api/app/api/v1/conversations.py` (rota de summary, linhas ~217-226), o `CreditTransaction` vira:

```python
        session.add(
            CreditTransaction(
                tenant_id=ctx.tenant_id,
                type="consumption",
                amount_credits=-credits,
                related_message_id=None,
                tokens_input=summary_result["tokens_input"] or None,
                tokens_output=summary_result["tokens_output"] or None,
                description=f"Resumo de conversa gerado ({tokens_used} tokens)",
            )
        )
```

- [ ] **Step 5: Gravar tokens brutos na conversa de teste** — em `apps/api/app/services/test_conversations.py` (linhas ~54-85), após `tokens_used = result["tokens_used"] or 0` acrescentar:

```python
    tokens_input = result.get("tokens_input", 0)
    tokens_output = result.get("tokens_output", 0)
```
e no `CreditTransaction`:
```python
            CreditTransaction(
                tenant_id=tenant_id,
                type="consumption",
                amount_credits=-credits,
                related_message_id=agent_messages[0].id,
                tokens_input=tokens_input or None,
                tokens_output=tokens_output or None,
                description=f"Consumo do agente em conversa de teste ({tokens_used} tokens)",
            )
```

- [ ] **Step 6: Atualizar os testes de rota que assertam o lançamento** — em `test_conversations_routes.py` e `test_test_conversations_routes.py`, os mocks de `generate_conversation_summary`/`send_playground_message` ganham as chaves novas; onde houver assert sobre o `CreditTransaction` criado, assertar também `tokens_input`/`tokens_output`.

- [ ] **Step 7: Rodar a suíte inteira + lint**

Run: `cd apps/api && uv run pytest tests/unit -v && uv run ruff check .`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/clients/agents.py apps/api/app/api/v1/conversations.py \
  apps/api/app/services/test_conversations.py apps/api/tests/unit/test_agents_client.py \
  apps/api/tests/unit/test_conversations_routes.py \
  apps/api/tests/unit/test_test_conversations_routes.py
git commit -m "feat(api): tokens brutos auditados no consumo de resumo e conversas de teste"
```

---

## Verificação final da etapa

- [ ] `cd apps/agents && uv run pytest tests/unit` → verde
- [ ] `cd apps/api && uv run ruff check . && uv run pytest tests/unit` → verde
- [ ] `cd apps/worker && uv run ruff check . && uv run pytest tests/unit` → verde
- [ ] `cd apps/api && uv run alembic upgrade head` idempotente (já em head)
- [ ] Atualizar `CLAUDE.md` (seção Billing / Créditos): registrar a fundação da Etapa 1 (pricing_configs, ledgers fracionados com resale/adjustment e auditoria de tokens; comportamento de débito inalterado) — commit `docs: registra fundação da wallet unificada (etapa 1) no CLAUDE.md`
