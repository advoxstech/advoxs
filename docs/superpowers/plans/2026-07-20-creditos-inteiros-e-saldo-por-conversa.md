# Créditos inteiros + saldo do cliente final por conversa — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Fazer todo desconto de créditos ser sempre um número inteiro (sem casas decimais), e (2) mostrar o saldo do cliente final de cada conversa no painel `/conversas`.

**Architecture:** Item 1 é uma mudança pontual no arredondamento final de `calcular_creditos` (função espelhada em `apps/api/app/services/pricing.py` e `apps/worker/app/pricing.py`), sem alteração de schema. Item 2 adiciona um campo computado (`end_customer_balance`) em `ConversationOut`, preenchido via lookup em `end_customer_balances` só quando a cobrança do cliente final está habilitada pro tenant, exibido no painel de conversas (`web`).

**Tech Stack:** FastAPI + SQLAlchemy async (`apps/api`), Arq (`apps/worker`), Next.js + Vitest (`apps/web`).

## Global Constraints

- Créditos sempre inteiros daqui pra frente — sem migração de histórico (transações já lançadas continuam com as casas decimais que já tinham).
- Sem mínimo de 1 crédito por cobrança — consumo muito barato pode arredondar pra 0 créditos, de propósito.
- `end_customer_balance` só é preenchido (não-nulo) quando `tenant_billing_settings.enabled = true` pro tenant — sem essa feature habilitada, o campo é sempre `null`.
- O saldo mostrado é o do **cliente final** (contato da conversa), nunca o saldo do escritório (`tenants.credit_balance`), que já é visível em outros lugares do painel.

Spec de referência: `docs/superpowers/specs/2026-07-20-creditos-inteiros-e-saldo-por-conversa-design.md`.

---

### Task 1: Créditos inteiros no `api`

**Files:**
- Modify: `apps/api/app/services/pricing.py`
- Modify: `apps/api/tests/unit/test_pricing_service.py`
- Modify: `apps/api/tests/unit/test_conversations_routes.py:388`
- Modify: `apps/api/tests/unit/test_test_conversations_routes.py:137`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: nenhuma dependência de outra task.
- Produces: `calcular_creditos(tokens_input: int, tokens_output: int, tokens_used: int, config) -> Decimal` — mesma assinatura, resultado agora sempre um `Decimal` inteiro (ex: `Decimal("2")`), nunca fracionado.

- [ ] **Step 1: Rodar os testes atuais de pricing pra confirmar o baseline**

Run: `cd apps/api && uv run pytest tests/unit/test_pricing_service.py -v`
Expected: PASS (4 testes, valores fracionados — ainda não mudamos nada).

- [ ] **Step 2: Reescrever `test_pricing_service.py` com os novos valores esperados (inteiros)**

Em `apps/api/tests/unit/test_pricing_service.py`, trocar as linhas 14-29:

```python
def test_pondera_input_e_output():
    # 2000*0.3 + 500*1.0 = 1100 tokens ponderados -> 1.1 créditos
    assert calcular_creditos(2000, 500, 2500, CONFIG) == Decimal("1.1000")


def test_arredonda_para_4_casas_half_up():
    assert calcular_creditos(1, 0, 1, CONFIG) == Decimal("0.0003")
    assert calcular_creditos(166, 0, 166, CONFIG) == Decimal("0.0498")


def test_fallback_sem_breakdown_trata_tudo_como_output():
    assert calcular_creditos(0, 0, 3500, CONFIG) == Decimal("3.5000")


def test_zero_tokens_zero_creditos():
    assert calcular_creditos(0, 0, 0, CONFIG) == Decimal("0.0000")
```

por:

```python
def test_pondera_input_e_output():
    # 2000*0.3 + 500*1.0 = 1100 tokens ponderados -> 1.1 créditos -> arredonda pra 1
    assert calcular_creditos(2000, 500, 2500, CONFIG) == Decimal("1")


def test_arredonda_para_inteiro_half_up():
    # 500*1.0/1000 = 0.5 créditos -> HALF_UP sobe pra 1
    assert calcular_creditos(0, 500, 500, CONFIG) == Decimal("1")
    # 1499*1.0/1000 = 1.499 créditos -> desce pra 1
    assert calcular_creditos(0, 1499, 1499, CONFIG) == Decimal("1")
    # 1500*1.0/1000 = 1.5 créditos -> HALF_UP sobe pra 2
    assert calcular_creditos(0, 1500, 1500, CONFIG) == Decimal("2")


def test_consumo_muito_barato_arredonda_pra_zero():
    # 1*0.3/1000 = 0.0003 créditos -> arredonda pra 0 (sem mínimo de 1 crédito)
    assert calcular_creditos(1, 0, 1, CONFIG) == Decimal("0")


def test_fallback_sem_breakdown_trata_tudo_como_output():
    # 3500 tokens como output / 1000 = 3.5 créditos -> HALF_UP sobe pra 4
    assert calcular_creditos(0, 0, 3500, CONFIG) == Decimal("4")


def test_zero_tokens_zero_creditos():
    assert calcular_creditos(0, 0, 0, CONFIG) == Decimal("0")
```

- [ ] **Step 2b: Rodar e confirmar que falha (o código ainda arredonda pra 4 casas)**

Run: `cd apps/api && uv run pytest tests/unit/test_pricing_service.py -v`
Expected: FAIL em `test_pondera_input_e_output`, `test_arredonda_para_inteiro_half_up`, `test_consumo_muito_barato_arredonda_pra_zero`, `test_fallback_sem_breakdown_trata_tudo_como_output` (valores ainda vêm com 4 casas decimais).

- [ ] **Step 3: Implementar — trocar a precisão de arredondamento**

Em `apps/api/app/services/pricing.py`, trocar:

```python
_PRECISION = Decimal("0.0001")
```

por:

```python
_PRECISION = Decimal("1")
```

E trocar a docstring de `calcular_creditos` (linhas atuais):

```python
def calcular_creditos(
    tokens_input: int, tokens_output: int, tokens_used: int, config: PricingConfig
) -> Decimal:
    """Créditos fracionados (4 casas, HALF_UP) a partir dos tokens ponderados.

    Espelha apps/worker/app/pricing.py (codebases separados, mesmo padrão da
    antiga env duplicada). Fallback de transição: breakdown zerado com
    tokens_used > 0 (agents antigo) trata tudo como output — cobra a mais,
    nunca a menos."""
```

por:

```python
def calcular_creditos(
    tokens_input: int, tokens_output: int, tokens_used: int, config: PricingConfig
) -> Decimal:
    """Créditos inteiros (arredondado pro mais próximo, HALF_UP) a partir dos
    tokens ponderados. Consumo muito barato pode arredondar pra 0 créditos —
    decisão deliberada, sem mínimo de 1 crédito por cobrança.

    Espelha apps/worker/app/pricing.py (codebases separados, mesmo padrão da
    antiga env duplicada). Fallback de transição: breakdown zerado com
    tokens_used > 0 (agents antigo) trata tudo como output — cobra a mais,
    nunca a menos."""
```

- [ ] **Step 4: Rodar os testes de pricing de novo e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_pricing_service.py -v`
Expected: PASS nos 6 testes.

- [ ] **Step 5: Atualizar os testes que dependem do valor calculado em outras rotas**

Em `apps/api/tests/unit/test_conversations_routes.py`, trocar (linha 385-388):

```python
        added = session.add.call_args.args[0]
        assert added.tenant_id == TENANT_ID
        assert added.type == "consumption"
        # 2000*0.3 + 500*1.0 = 1100 tokens ponderados -> 1.1 créditos
        assert added.amount_credits == Decimal("-1.1000")
```

por:

```python
        added = session.add.call_args.args[0]
        assert added.tenant_id == TENANT_ID
        assert added.type == "consumption"
        # 2000*0.3 + 500*1.0 = 1100 tokens ponderados -> 1.1 créditos -> arredonda pra 1
        assert added.amount_credits == Decimal("-1")
```

Em `apps/api/tests/unit/test_test_conversations_routes.py`, trocar (linhas 133-137):

```python
        # Último add é o lançamento do ledger — com os tokens brutos auditados.
        transaction = session.add.call_args.args[0]
        assert transaction.type == "consumption"
        # 2800*0.3 + 700*1.0 = 1540 tokens ponderados -> 1.54 créditos
        assert transaction.amount_credits == Decimal("-1.5400")
```

por:

```python
        # Último add é o lançamento do ledger — com os tokens brutos auditados.
        transaction = session.add.call_args.args[0]
        assert transaction.type == "consumption"
        # 2800*0.3 + 700*1.0 = 1540 tokens ponderados -> 1.54 créditos -> arredonda pra 2
        assert transaction.amount_credits == Decimal("-2")
```

- [ ] **Step 6: Rodar a suíte completa do `api` e confirmar que tudo passa**

Run: `cd apps/api && uv run pytest tests/unit -v && uv run ruff check .`
Expected: todos os testes passam (nenhuma regressão), `ruff check` sem erros.

- [ ] **Step 7: Atualizar `CLAUDE.md`**

Em `CLAUDE.md`, na seção "Regra de consumo", trocar (linhas 342-344):

```markdown
- **Não é flat por mensagem e não é mais arredondado pra cima.** Consumo é calculado a partir do custo real de cada execução, com créditos **fracionados**:
  - Tokens de input e output, com **pesos diferentes** (`input_weight`/`output_weight`, refletindo o custo real da API) — `tokens_ponderados = tokens_input × input_weight + tokens_output × output_weight`.
  - `créditos = tokens_ponderados / tokens_per_credit`, arredondado a **4 casas decimais** (`ROUND_HALF_UP`) — nunca `ceil`, nunca cobra a mais por fração.
```

por:

```markdown
- **Não é flat por mensagem.** Consumo é calculado a partir do custo real de cada execução, sempre em **créditos inteiros**:
  - Tokens de input e output, com **pesos diferentes** (`input_weight`/`output_weight`, refletindo o custo real da API) — `tokens_ponderados = tokens_input × input_weight + tokens_output × output_weight`.
  - `créditos = tokens_ponderados / tokens_per_credit`, arredondado pro **inteiro mais próximo** (`ROUND_HALF_UP`, simétrico — não é `ceil` sistemático) — sem casas decimais. Consumo muito barato pode arredondar pra 0 créditos (sem mínimo de 1 crédito por cobrança, decisão deliberada). Sem migração de histórico: transações já lançadas antes dessa mudança continuam com as casas decimais que já tinham.
```

- [ ] **Step 8: Commit**

```bash
cd /home/falcao/development/advoxs
git add apps/api/app/services/pricing.py apps/api/tests/unit/test_pricing_service.py apps/api/tests/unit/test_conversations_routes.py apps/api/tests/unit/test_test_conversations_routes.py CLAUDE.md
git commit -m "feat(api): créditos sempre inteiros — arredonda pro inteiro mais próximo em vez de 4 casas"
```

---

### Task 2: Créditos inteiros no `worker`

**Files:**
- Modify: `apps/worker/app/pricing.py`
- Modify: `apps/worker/tests/unit/test_pricing.py`
- Modify: `apps/worker/tests/unit/test_process_inbound_message.py:102-118`

**Interfaces:**
- Consumes: nenhuma dependência da Task 1 (arquivo espelhado, independente).
- Produces: mesma interface da Task 1, espelhada em `apps/worker/app/pricing.py`.

- [ ] **Step 1: Rodar os testes atuais pra confirmar o baseline**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_pricing.py -v`
Expected: PASS (4 testes, valores fracionados).

- [ ] **Step 2: Reescrever `test_pricing.py` com os novos valores esperados**

Em `apps/worker/tests/unit/test_pricing.py`, trocar o arquivo inteiro (a partir da linha 11, mantendo o topo do arquivo — imports e `CONFIG` — igual):

```python
def test_pondera_input_e_output():
    # 2000*0.3 + 500*1.0 = 1100 tokens ponderados -> 1.1 créditos
    assert calcular_creditos(2000, 500, 2500, CONFIG) == Decimal("1.1000")


def test_arredonda_para_4_casas_half_up():
    # 1*0.3 = 0.3 tokens ponderados -> 0.0003 créditos
    assert calcular_creditos(1, 0, 1, CONFIG) == Decimal("0.0003")
    # 166*0.3 = 49.8 -> 0.0498
    assert calcular_creditos(166, 0, 166, CONFIG) == Decimal("0.0498")


def test_fallback_sem_breakdown_trata_tudo_como_output():
    # agents antigo: breakdown zerado mas tokens_used > 0 -> peso 1.0 (cobra
    # a mais, nunca a menos, só durante a transição de deploy)
    assert calcular_creditos(0, 0, 3500, CONFIG) == Decimal("3.5000")


def test_zero_tokens_zero_creditos():
    assert calcular_creditos(0, 0, 0, CONFIG) == Decimal("0.0000")
```

por:

```python
def test_pondera_input_e_output():
    # 2000*0.3 + 500*1.0 = 1100 tokens ponderados -> 1.1 créditos -> arredonda pra 1
    assert calcular_creditos(2000, 500, 2500, CONFIG) == Decimal("1")


def test_arredonda_para_inteiro_half_up():
    # 500*1.0/1000 = 0.5 créditos -> HALF_UP sobe pra 1
    assert calcular_creditos(0, 500, 500, CONFIG) == Decimal("1")
    # 1499*1.0/1000 = 1.499 créditos -> desce pra 1
    assert calcular_creditos(0, 1499, 1499, CONFIG) == Decimal("1")
    # 1500*1.0/1000 = 1.5 créditos -> HALF_UP sobe pra 2
    assert calcular_creditos(0, 1500, 1500, CONFIG) == Decimal("2")


def test_consumo_muito_barato_arredonda_pra_zero():
    # 1*0.3/1000 = 0.0003 créditos -> arredonda pra 0 (sem mínimo de 1 crédito)
    assert calcular_creditos(1, 0, 1, CONFIG) == Decimal("0")


def test_fallback_sem_breakdown_trata_tudo_como_output():
    # agents antigo: breakdown zerado mas tokens_used > 0 -> peso 1.0. 3500/1000
    # = 3.5 créditos -> HALF_UP sobe pra 4 (cobra a mais, só na transição de deploy)
    assert calcular_creditos(0, 0, 3500, CONFIG) == Decimal("4")


def test_zero_tokens_zero_creditos():
    assert calcular_creditos(0, 0, 0, CONFIG) == Decimal("0")
```

- [ ] **Step 2b: Rodar e confirmar que falha**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_pricing.py -v`
Expected: FAIL nos testes com valor recalculado (código ainda arredonda pra 4 casas).

- [ ] **Step 3: Implementar — mesma mudança da Task 1, espelhada**

Em `apps/worker/app/pricing.py`, trocar:

```python
_PRECISION = Decimal("0.0001")
```

por:

```python
_PRECISION = Decimal("1")
```

E trocar a docstring de `calcular_creditos`:

```python
def calcular_creditos(tokens_input: int, tokens_output: int, tokens_used: int, config) -> Decimal:
    """Créditos fracionados (4 casas, HALF_UP) a partir dos tokens ponderados.

    Fallback de transição: breakdown zerado com tokens_used > 0 (agents antigo)
    trata tudo como output — cobra a mais, nunca a menos."""
```

por:

```python
def calcular_creditos(tokens_input: int, tokens_output: int, tokens_used: int, config) -> Decimal:
    """Créditos inteiros (arredondado pro mais próximo, HALF_UP) a partir dos
    tokens ponderados. Consumo muito barato pode arredondar pra 0 créditos —
    decisão deliberada, sem mínimo de 1 crédito por cobrança.

    Fallback de transição: breakdown zerado com tokens_used > 0 (agents antigo)
    trata tudo como output — cobra a mais, nunca a menos."""
```

- [ ] **Step 4: Rodar os testes de pricing de novo e confirmar que passam**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_pricing.py -v`
Expected: PASS nos 6 testes.

- [ ] **Step 5: Atualizar `test_process_inbound_message.py`**

Em `apps/worker/tests/unit/test_process_inbound_message.py`, trocar (linhas 102-118):

```python
async def test_consumo_ponderado_fracionado(patched) -> None:
    # 2500*0.3 + 1000*1.0 = 1750 tokens ponderados / 1000 = 1.75 créditos
    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    persist_args = patched["persist"].await_args.args
    assert persist_args[4] == 3500  # tokens_used
    assert persist_args[5] == Decimal("1.7500")  # credits ponderados
    patched["debitar"].assert_awaited_once_with(
        patched["debitar"].await_args.args[0],
        TENANT_ID,
        FIRST_MESSAGE_ID,
        3500,
        Decimal("1.7500"),
        2500,
        1000,
        PRICING_CONFIG.id,
    )
    # Sem cobrança do cliente final: só o estoque do tenant é debitado.
    patched["debitar_cliente_final"].assert_not_awaited()
```

por:

```python
async def test_consumo_ponderado_arredonda_pro_inteiro(patched) -> None:
    # 2500*0.3 + 1000*1.0 = 1750 tokens ponderados / 1000 = 1.75 créditos ->
    # HALF_UP sobe pra 2
    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    persist_args = patched["persist"].await_args.args
    assert persist_args[4] == 3500  # tokens_used
    assert persist_args[5] == Decimal("2")  # credits arredondados
    patched["debitar"].assert_awaited_once_with(
        patched["debitar"].await_args.args[0],
        TENANT_ID,
        FIRST_MESSAGE_ID,
        3500,
        Decimal("2"),
        2500,
        1000,
        PRICING_CONFIG.id,
    )
    # Sem cobrança do cliente final: só o estoque do tenant é debitado.
    patched["debitar_cliente_final"].assert_not_awaited()
```

Nota: `test_moeda_unica_debita_so_o_cliente_final` (mais abaixo no mesmo arquivo, `assert args[5] == Decimal("2.0000")`) **não precisa mudar** — 2000 tokens sem breakdown já caía em 2000/1000 = 2.0 créditos, que já era um número inteiro; `Decimal("2") == Decimal("2.0000")` continua `True` (comparação por valor, não por representação).

- [ ] **Step 6: Rodar a suíte completa do `worker` e confirmar que tudo passa**

Run: `cd apps/worker && python3 -m pytest tests/unit -v`
Expected: todos os testes passam (nenhuma regressão).

- [ ] **Step 7: Commit**

```bash
cd /home/falcao/development/advoxs
git add apps/worker/app/pricing.py apps/worker/tests/unit/test_pricing.py apps/worker/tests/unit/test_process_inbound_message.py
git commit -m "feat(worker): créditos sempre inteiros — espelha a mudança de arredondamento do api"
```

---

### Task 3: Saldo do cliente final por conversa — backend (`api`)

**Files:**
- Modify: `apps/api/app/schemas/conversations.py`
- Modify: `apps/api/app/api/v1/conversations.py`
- Test: `apps/api/tests/unit/test_conversations_routes.py`

**Interfaces:**
- Consumes: modelos `TenantBillingSettings` (`tenant_id`, `enabled`) e `EndCustomerBalance` (`tenant_id`, `contact_phone_number`, `credit_balance`), já exportados por `app.models`.
- Produces: `ConversationOut.end_customer_balance: float | None` — novo campo no schema, preenchido por `list_conversations`, `update_state` e `generate_summary`. Função auxiliar `_end_customer_balances_by_phone(session, tenant_id, phone_numbers) -> dict[str, Decimal]` e `_to_conversation_out(conversation, end_customer_balance) -> ConversationOut`, ambas em `apps/api/app/api/v1/conversations.py` — não exportadas, só usadas dentro do módulo.

- [ ] **Step 1: Adicionar o campo novo em `ConversationOut`**

Em `apps/api/app/schemas/conversations.py`, trocar (linhas 8-18):

```python
class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_phone_number: str
    state: Literal["agent", "human"]
    is_test: bool
    last_message_at: datetime | None
    created_at: datetime
    summary: str | None
    summary_generated_at: datetime | None
```

por:

```python
class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_phone_number: str
    state: Literal["agent", "human"]
    is_test: bool
    last_message_at: datetime | None
    created_at: datetime
    summary: str | None
    summary_generated_at: datetime | None
    end_customer_balance: float | None = None
```

- [ ] **Step 2: Escrever os testes (falhando) em `test_conversations_routes.py`**

Em `apps/api/tests/unit/test_conversations_routes.py`, adicionar depois de `_execute_returning` (linha 89, antes de `def test_sem_token_retorna_401`):

```python
def _balance_result(rows: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result
```

E adicionar ao final do arquivo (depois da classe `TestDeleteConversation`, mantendo a mesma estrutura de fixtures `client`/`session`/`_conversation` já usada no resto do arquivo):

```python


class TestEndCustomerBalance:
    def test_lista_inclui_saldo_do_cliente_final_quando_ha_registro(
        self, client, session
    ) -> None:
        session.execute.side_effect = [
            _execute_returning([_conversation()]),
            _balance_result(
                [
                    SimpleNamespace(
                        contact_phone_number="5511999998888", credit_balance=Decimal("42")
                    )
                ]
            ),
        ]

        response = client.get("/api/v1/conversations")

        assert response.status_code == 200
        assert response.json()[0]["end_customer_balance"] == 42.0

    def test_lista_sem_saldo_encontrado_retorna_null(self, client, session) -> None:
        session.execute.side_effect = [
            _execute_returning([_conversation()]),
            _balance_result([]),
        ]

        response = client.get("/api/v1/conversations")

        assert response.status_code == 200
        assert response.json()[0]["end_customer_balance"] is None

    def test_lista_vazia_nao_consulta_saldo(self, client, session) -> None:
        session.execute.return_value = _execute_returning([])

        response = client.get("/api/v1/conversations")

        assert response.status_code == 200
        assert response.json() == []
        # Sem conversas, não há telefone pra buscar saldo — só 1 chamada a execute.
        session.execute.assert_awaited_once()

    def test_takeover_devolve_saldo_do_cliente_final(self, client, session) -> None:
        conversation = _conversation(state="agent")
        session.scalar.return_value = conversation
        session.execute.side_effect = [
            _balance_result(
                [
                    SimpleNamespace(
                        contact_phone_number="5511999998888", credit_balance=Decimal("7")
                    )
                ]
            ),
        ]

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}", json={"state": "human"}
        )

        assert response.status_code == 200
        assert response.json()["end_customer_balance"] == 7.0
```

- [ ] **Step 3: Rodar os testes novos e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py::TestEndCustomerBalance -v`
Expected: FAIL — `KeyError: 'end_customer_balance'` ou o campo vem sempre `None` mesmo quando deveria ter valor (a lógica ainda não existe).

- [ ] **Step 4: Implementar — imports novos em `conversations.py`**

Em `apps/api/app/api/v1/conversations.py`, trocar as linhas 1-39:

```python
"""Painel de conversas: listagem, histórico, takeover, resposta humana e resumo sob demanda."""

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.agents import (
    AgentsApiError,
    AgentsNetworkError,
    delete_agent_checkpoint,
    generate_conversation_summary,
    sync_conversation_context,
)
from app.clients.whatsapp import WhatsAppSendError, send_text_message
from app.core.crypto import decrypt_access_token
from app.models import (
    Conversation,
    CreditTransaction,
    EndCustomerCreditTransaction,
    Message,
    Tenant,
    WhatsAppNumber,
)
from app.schemas.conversations import (
    ConversationOut,
    ConversationStateUpdate,
    ConversationUsageOut,
    MessageOut,
    SendMessageRequest,
)
from app.services.conversations_usage import build_conversations_usage
from app.services.pricing import calcular_creditos, get_current_pricing_config

router = APIRouter(prefix="/conversations", tags=["conversations"])
logger = logging.getLogger(__name__)
```

por:

```python
"""Painel de conversas: listagem, histórico, takeover, resposta humana e resumo sob demanda."""

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.agents import (
    AgentsApiError,
    AgentsNetworkError,
    delete_agent_checkpoint,
    generate_conversation_summary,
    sync_conversation_context,
)
from app.clients.whatsapp import WhatsAppSendError, send_text_message
from app.core.crypto import decrypt_access_token
from app.models import (
    Conversation,
    CreditTransaction,
    EndCustomerBalance,
    EndCustomerCreditTransaction,
    Message,
    Tenant,
    TenantBillingSettings,
    WhatsAppNumber,
)
from app.schemas.conversations import (
    ConversationOut,
    ConversationStateUpdate,
    ConversationUsageOut,
    MessageOut,
    SendMessageRequest,
)
from app.services.conversations_usage import build_conversations_usage
from app.services.pricing import calcular_creditos, get_current_pricing_config

router = APIRouter(prefix="/conversations", tags=["conversations"])
logger = logging.getLogger(__name__)
```

- [ ] **Step 5: Implementar — helpers novos e wiring nas 3 rotas**

No mesmo arquivo, trocar a rota `list_conversations` (linhas 45-63 do arquivo original):

```python
@router.get("")
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    origin: Literal["real", "test"] = Query(default="real"),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ConversationOut]:
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.tenant_id == ctx.tenant_id,
            Conversation.is_test == (origin == "test"),
        )
        .order_by(Conversation.last_message_at.desc().nulls_last())
        .limit(limit)
        .offset(offset)
    )
    return [ConversationOut.model_validate(c) for c in result.scalars().all()]
```

por:

```python
@router.get("")
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    origin: Literal["real", "test"] = Query(default="real"),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ConversationOut]:
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.tenant_id == ctx.tenant_id,
            Conversation.is_test == (origin == "test"),
        )
        .order_by(Conversation.last_message_at.desc().nulls_last())
        .limit(limit)
        .offset(offset)
    )
    conversations = result.scalars().all()
    balances = await _end_customer_balances_by_phone(
        session, ctx.tenant_id, [c.contact_phone_number for c in conversations]
    )
    return [
        _to_conversation_out(c, balances.get(c.contact_phone_number)) for c in conversations
    ]
```

Trocar a rota `update_state` (linhas 104-118 do arquivo original):

```python
@router.patch("/{conversation_id}")
async def update_state(
    conversation_id: uuid.UUID,
    body: ConversationStateUpdate,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ConversationOut:
    """Toggle de takeover: em modo `human`, o worker não aciona o agente."""
    conversation = await _get_conversation(conversation_id, ctx, session)
    conversation.state = body.state
    if body.state == "human":
        # Takeover começa "presente" — o heartbeat do painel mantém depois.
        conversation.human_last_seen_at = datetime.now(UTC)
    await session.commit()
    return ConversationOut.model_validate(conversation)
```

por:

```python
@router.patch("/{conversation_id}")
async def update_state(
    conversation_id: uuid.UUID,
    body: ConversationStateUpdate,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ConversationOut:
    """Toggle de takeover: em modo `human`, o worker não aciona o agente."""
    conversation = await _get_conversation(conversation_id, ctx, session)
    conversation.state = body.state
    if body.state == "human":
        # Takeover começa "presente" — o heartbeat do painel mantém depois.
        conversation.human_last_seen_at = datetime.now(UTC)
    await session.commit()
    balances = await _end_customer_balances_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    return _to_conversation_out(conversation, balances.get(conversation.contact_phone_number))
```

Trocar o final da rota `generate_summary` — as últimas duas linhas da função (originais):

```python
    await session.commit()
    return ConversationOut.model_validate(conversation)
```

por:

```python
    await session.commit()
    balances = await _end_customer_balances_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    return _to_conversation_out(conversation, balances.get(conversation.contact_phone_number))
```

(Atenção: essa string `await session.commit()\n    return ConversationOut.model_validate(conversation)` aparece só uma vez em `generate_summary` — não confundir com a de `update_state`, que já foi trocada no passo anterior e por isso não bate mais com esse texto exato.)

Por fim, adicionar os dois helpers novos imediatamente antes de `async def _get_conversation(...)` (função privada no fim do arquivo):

```python
async def _end_customer_balances_by_phone(
    session: AsyncSession, tenant_id: uuid.UUID, phone_numbers: list[str]
) -> dict[str, Decimal]:
    """Saldo do cliente final por contato — só populado quando a cobrança do
    cliente final está habilitada pro tenant; caso contrário (ou sem contatos
    pra buscar) retorna {} e o campo do conversation fica None."""
    if not phone_numbers:
        return {}
    result = await session.execute(
        select(EndCustomerBalance.contact_phone_number, EndCustomerBalance.credit_balance)
        .join(
            TenantBillingSettings,
            TenantBillingSettings.tenant_id == EndCustomerBalance.tenant_id,
        )
        .where(
            TenantBillingSettings.enabled.is_(True),
            EndCustomerBalance.tenant_id == tenant_id,
            EndCustomerBalance.contact_phone_number.in_(phone_numbers),
        )
    )
    return {row.contact_phone_number: row.credit_balance for row in result.all()}


def _to_conversation_out(
    conversation: Conversation, end_customer_balance: Decimal | None
) -> ConversationOut:
    out = ConversationOut.model_validate(conversation)
    out.end_customer_balance = (
        float(end_customer_balance) if end_customer_balance is not None else None
    )
    return out


```

- [ ] **Step 6: Rodar os testes novos e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py::TestEndCustomerBalance -v`
Expected: PASS nos 4 testes.

- [ ] **Step 7: Rodar a suíte completa do `api` e o lint**

Run: `cd apps/api && uv run pytest tests/unit -v && uv run ruff check .`
Expected: todos os testes passam (nenhuma regressão em `TestListConversations`, `TestOriginFilter`, `TestTakeover`, `TestHeartbeat`, `TestPatchSetaPresenca`, `TestGenerateSummary`, `TestDeleteConversation` — nenhum deles inspeciona o novo campo, e a chamada extra a `session.execute`/`session.scalar` não quebra os mocks existentes: quando não configurada explicitamente, a chamada extra bate num `MagicMock` sem comportamento definido, cuja iteração padrão do `unittest.mock` já é vazia). `test_test_conversations_routes.py::TestCreate::test_cria_conversa_de_teste` continua passando sem alteração — confirma que `ConversationOut.model_validate(conversation)` usa o default `None` do campo novo quando o objeto ORM não tem esse atributo (rota de conversa de teste não foi tocada nesta task).

- [ ] **Step 8: Commit**

```bash
cd /home/falcao/development/advoxs
git add apps/api/app/schemas/conversations.py apps/api/app/api/v1/conversations.py apps/api/tests/unit/test_conversations_routes.py
git commit -m "feat(api): expõe end_customer_balance por conversa em ConversationOut"
```

---

### Task 4: Saldo do cliente final por conversa — frontend, lista de conversas

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/components/ConversationList.tsx`
- Test: `apps/web/__tests__/ConversationList.test.tsx`

**Interfaces:**
- Consumes: `end_customer_balance` já vem em cada item de `GET /api/v1/conversations` (Task 3).
- Produces: `Conversation.end_customer_balance?: number | null` — campo opcional, pra não exigir tocar em nenhuma fixture de teste já existente que constrói um `Conversation` sem esse campo.

- [ ] **Step 1: Adicionar o campo opcional em `types.ts`**

Em `apps/web/src/lib/types.ts`, trocar (linhas 3-12):

```ts
export interface Conversation {
  id: string;
  contact_phone_number: string;
  state: ConversationState;
  is_test: boolean;
  last_message_at: string | null;
  created_at: string;
  summary: string | null;
  summary_generated_at: string | null;
}
```

por:

```ts
export interface Conversation {
  id: string;
  contact_phone_number: string;
  state: ConversationState;
  is_test: boolean;
  last_message_at: string | null;
  created_at: string;
  summary: string | null;
  summary_generated_at: string | null;
  end_customer_balance?: number | null;
}
```

- [ ] **Step 2: Escrever os testes (falhando) em `ConversationList.test.tsx`**

Em `apps/web/__tests__/ConversationList.test.tsx`, adicionar ao final do `describe("ConversationList", ...)` (depois do teste "mostra estado vazio quando carregou sem conversas", antes do `});` de fechamento):

```tsx

  it("mostra o saldo do cliente final quando presente", () => {
    render(
      <ConversationList
        conversations={[{ ...conversations[0], end_customer_balance: 128.5 }]}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText("128,5 créditos")).toBeInTheDocument();
  });

  it("não mostra saldo quando end_customer_balance é null", () => {
    render(
      <ConversationList
        conversations={[{ ...conversations[0], end_customer_balance: null }]}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.queryByText(/créditos/)).not.toBeInTheDocument();
  });
```

- [ ] **Step 3: Rodar os testes novos e confirmar que falham**

Run: `cd apps/web && pnpm vitest run __tests__/ConversationList.test.tsx`
Expected: FAIL no primeiro teste novo — `Unable to find an element with the text: 128,5 créditos` (o componente ainda não exibe o saldo).

- [ ] **Step 4: Implementar — import e JSX em `ConversationList.tsx`**

Em `apps/web/src/components/ConversationList.tsx`, trocar a linha 3:

```tsx
import { formatMessageTime, formatPhone } from "@/lib/format";
```

por:

```tsx
import { formatCredits, formatMessageTime, formatPhone } from "@/lib/format";
```

E trocar o bloco do indicador de estado (linhas 57-69 do arquivo original):

```tsx
              <span
                className={`flex items-center gap-1.5 text-xs ${
                  isManual ? "text-brass" : "text-muted"
                }`}
              >
                <span
                  aria-hidden
                  className={`h-1.5 w-1.5 rounded-full ${
                    isManual ? "bg-brass" : "bg-accent"
                  }`}
                />
                {isManual ? "atendimento manual" : "agente respondendo"}
              </span>
```

por:

```tsx
              <span className="flex items-center justify-between gap-2">
                <span
                  className={`flex items-center gap-1.5 text-xs ${
                    isManual ? "text-brass" : "text-muted"
                  }`}
                >
                  <span
                    aria-hidden
                    className={`h-1.5 w-1.5 rounded-full ${
                      isManual ? "bg-brass" : "bg-accent"
                    }`}
                  />
                  {isManual ? "atendimento manual" : "agente respondendo"}
                </span>
                {conversation.end_customer_balance != null ? (
                  <span className="font-mono text-[11px] text-muted">
                    {formatCredits(conversation.end_customer_balance)} créditos
                  </span>
                ) : null}
              </span>
```

- [ ] **Step 5: Rodar os testes novos de novo e confirmar que passam**

Run: `cd apps/web && pnpm vitest run __tests__/ConversationList.test.tsx`
Expected: PASS nos 5 testes do arquivo (os 2 novos + os 3 já existentes, sem regressão).

- [ ] **Step 6: Commit**

```bash
cd /home/falcao/development/advoxs
git add apps/web/src/lib/types.ts apps/web/src/components/ConversationList.tsx apps/web/__tests__/ConversationList.test.tsx
git commit -m "feat(web): mostra o saldo do cliente final na lista de conversas"
```

---

### Task 5: Saldo do cliente final por conversa — frontend, cabeçalho da thread + doc

**Files:**
- Modify: `apps/web/src/components/ConversationThread.tsx`
- Modify: `CLAUDE.md`
- Test: `apps/web/__tests__/ConversationThread.test.tsx`

**Interfaces:**
- Consumes: `Conversation.end_customer_balance?: number | null` (Task 4).
- Produces: nenhuma interface nova — última peça visível da feature.

- [ ] **Step 1: Escrever os testes (falhando) em `ConversationThread.test.tsx`**

Em `apps/web/__tests__/ConversationThread.test.tsx`, adicionar ao final do `describe("ConversationThread", ...)` (depois do último teste do arquivo, antes do `});` de fechamento):

```tsx

  it("mostra o saldo do cliente final quando presente", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={{ ...conversation("agent"), end_customer_balance: 50 }}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    expect(screen.getByText(/saldo do cliente: 50 créditos/)).toBeInTheDocument();
  });

  it("não mostra saldo do cliente quando end_customer_balance é null", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={{ ...conversation("agent"), end_customer_balance: null }}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    expect(screen.queryByText(/saldo do cliente/)).not.toBeInTheDocument();
  });
```

- [ ] **Step 2: Rodar os testes novos e confirmar que falham**

Run: `cd apps/web && pnpm vitest run __tests__/ConversationThread.test.tsx`
Expected: FAIL no primeiro teste novo — `Unable to find an element with the text: /saldo do cliente: 50 créditos/`.

- [ ] **Step 3: Implementar — import e JSX em `ConversationThread.tsx`**

Em `apps/web/src/components/ConversationThread.tsx`, trocar a linha 6:

```tsx
import { formatFullDateTime, formatMessageTime, formatPhone } from "@/lib/format";
```

por:

```tsx
import { formatCredits, formatFullDateTime, formatMessageTime, formatPhone } from "@/lib/format";
```

E trocar o bloco do cabeçalho (linhas 187-201 do arquivo original):

```tsx
        <div className="flex items-center gap-4">
          <h2 className="font-mono text-sm font-medium">
            {formatPhone(conversation.contact_phone_number)}
          </h2>
          {isManual ? (
            <span className="-rotate-2 select-none border-[3px] border-double border-brass px-2 py-0.5 font-mono text-[11px] font-medium uppercase tracking-[0.18em] text-brass">
              Atendimento manual
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs text-muted">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-accent" />
              agente respondendo
            </span>
          )}
        </div>
```

por:

```tsx
        <div className="flex items-center gap-4">
          <h2 className="font-mono text-sm font-medium">
            {formatPhone(conversation.contact_phone_number)}
          </h2>
          {isManual ? (
            <span className="-rotate-2 select-none border-[3px] border-double border-brass px-2 py-0.5 font-mono text-[11px] font-medium uppercase tracking-[0.18em] text-brass">
              Atendimento manual
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs text-muted">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-accent" />
              agente respondendo
            </span>
          )}
          {conversation.end_customer_balance != null ? (
            <span className="font-mono text-xs text-muted">
              saldo do cliente: {formatCredits(conversation.end_customer_balance)} créditos
            </span>
          ) : null}
        </div>
```

- [ ] **Step 4: Rodar os testes novos de novo e confirmar que passam**

Run: `cd apps/web && pnpm vitest run __tests__/ConversationThread.test.tsx`
Expected: PASS em todos os testes do arquivo (os 2 novos + os já existentes, sem regressão).

- [ ] **Step 5: Rodar a suíte completa do `web`, lint e build**

Run: `cd apps/web && pnpm test && pnpm lint && pnpm build`
Expected: tudo verde.

- [ ] **Step 6: Atualizar `CLAUDE.md`**

Em `CLAUDE.md`, na seção "Painel de Conversas" (`/conversas`), no bullet "✅ **Front pronto em `/conversas`**" (o parágrafo grande que descreve a lista/thread), adicionar ao final da frase que descreve o toggle de takeover e o resumo — logo depois de "...bloqueado com aviso quando o saldo de créditos está esgotado)." — a frase:

```
Quando a cobrança do cliente final está habilitada pro tenant, cada conversa mostra o saldo de créditos daquele contato específico (lista e cabeçalho da thread) — `end_customer_balance` em `ConversationOut`, `null` quando a feature não está habilitada ou o contato ainda não tem registro de saldo.
```

- [ ] **Step 7: Commit**

```bash
cd /home/falcao/development/advoxs
git add apps/web/src/components/ConversationThread.tsx apps/web/__tests__/ConversationThread.test.tsx CLAUDE.md
git commit -m "feat(web): mostra o saldo do cliente final no cabeçalho da conversa"
```

---

## Verificação final

- [ ] **Step 1: Rodar as duas suítes completas de novo, do zero**

Run: `cd apps/api && uv run pytest tests/unit -v && uv run ruff check .`
Run: `cd apps/worker && python3 -m pytest tests/unit -v`
Run: `cd apps/web && pnpm test && pnpm lint && pnpm build`

Expected: tudo verde.

- [ ] **Step 2: Teste manual rápido (dev local)**

Com o stack local no ar, criar/usar um tenant com cobrança do cliente final habilitada e algum saldo em `end_customer_balances`, abrir `/conversas`, e confirmar visualmente que o saldo aparece na lista e no cabeçalho da thread daquele contato — e que, pra um tenant sem a feature habilitada, nenhum saldo aparece em lugar nenhum.
