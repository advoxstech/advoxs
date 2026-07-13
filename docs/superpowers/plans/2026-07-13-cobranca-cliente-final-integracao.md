# Cobrança do Cliente Final — Integração (`worker` + `agents` + `web`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Pré-requisito:** este plano depende das rotas/tabelas criadas em `docs/superpowers/plans/2026-07-13-cobranca-cliente-final-api.md` (Plano 1) já mergeadas — em especial `POST /api/v1/internal/end-customer-billing/checkout` (Task 8 do Plano 1) e as tabelas `tenant_billing_settings`/`end_customer_credit_packages`/`end_customer_balances`/`end_customer_credit_transactions` (Task 1 do Plano 1).

**Goal:** Ligar o gate de saldo do cliente final e a oferta de pacotes dentro da conversa do WhatsApp (`worker`+`agents`), e dar ao tenant um painel (`web`) pra configurar a própria Stripe e os próprios pacotes.

**Architecture:** O `worker` lê o saldo/pacotes do cliente final antes de chamar o `agents` (mesmo lugar onde já lê o `credit_balance` do tenant) e inclui isso por valor no contrato `POST /messages` — nenhuma chamada HTTP nova nesse caminho. Dentro do `agents`, o grafo LangGraph ganha um gate técnico em `transfer_to_specialist` (bloqueia a transferência sem saldo) e uma tool nova (`gerar_link_pagamento_cliente`) que chama de volta o endpoint interno do `api` (Plano 1) pra gerar o link — a secretária nunca vê a secret key do tenant. Depois da resposta, o `worker` debita o saldo do cliente final na mesma transação que já debita o crédito do tenant. No `web`, uma página nova em `/configuracoes/cobranca-clientes` consome as rotas de settings/pacotes do Plano 1.

**Tech Stack:** Arq (worker), LangGraph + FastAPI (agents), Next.js/React (web), pytest/pytest-asyncio (worker, agents), Vitest + Testing Library (web).

## Global Constraints

- `worker` e `agents` são deployables separados sem código Python compartilhado — toda leitura/escrita em `tenant_billing_settings`/`end_customer_balances`/`end_customer_credit_packages`/`end_customer_credit_transactions` no `worker` usa as `Table` do próprio `apps/worker/app/tables.py` (Core, não ORM), mesmo padrão já usado pro `credit_balance` do tenant.
- Nenhum dado sensível (secret key/webhook secret da Stripe) trafega pro `agents` em nenhum momento — o `agents` só recebe `enabled`/`balance`/lista de pacotes (id/nome/preço/créditos), tudo não sensível.
- O gate de saldo do cliente final (bloquear `transfer_to_specialist`) só é aplicado quando `end_customer_billing.enabled=true` — com a feature desligada pro tenant, o comportamento é idêntico ao de hoje, sem nenhuma checagem.
- `tool_node` nunca confia em valor de saldo/`enabled` vindo do LLM — sempre injetado do `state`, mesmo princípio já usado pelos `STATE_SCOPED_TOOLS` pro `conversation_id`.
- Débito do cliente final só ocorre se o saldo **já era positivo antes da chamada** ao `agents` — saldo zerado não gera débito (a interação foi só a secretária oferecendo pacotes).
- Web: `backendFetch` é mockado diretamente nos testes (`vi.mock("@/lib/client-api")`), não MSW — siga o padrão já usado em `WhatsAppConnectionPanel.test.tsx`/`TenantNav.test.tsx`.
- Comandos: `uv run pytest tests/unit -q && uv run ruff check .` (dentro de `apps/worker` e de `apps/agents`); `pnpm test && pnpm lint` (dentro de `apps/web`).

---

### Task 1: `worker` — tabelas novas em `app/tables.py`

**Files:**
- Modify: `apps/worker/app/tables.py`

**Interfaces:**
- Produces: `tables.tenant_billing_settings`, `tables.end_customer_credit_packages`, `tables.end_customer_balances`, `tables.end_customer_credit_transactions` — usadas pelas Tasks 3 e 4.

- [ ] **Step 1: Adicionar as tabelas (sem teste dedicado — mesmo padrão do restante do arquivo, que só declara colunas Core)**

Adicionar ao final de `apps/worker/app/tables.py`:

```python
tenant_billing_settings = Table(
    "tenant_billing_settings",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("enabled", Boolean),
    Column("end_customer_tokens_per_credit", Integer),
)

end_customer_credit_packages = Table(
    "end_customer_credit_packages",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("name", String),
    Column("price_brl", Numeric(10, 2)),
    Column("credits_granted", Integer),
    Column("active", Boolean),
)

end_customer_balances = Table(
    "end_customer_balances",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("contact_phone_number", String),
    Column("credit_balance", Integer),
)

end_customer_credit_transactions = Table(
    "end_customer_credit_transactions",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("tenant_id", Uuid),
    Column("contact_phone_number", String),
    Column("type", String),
    Column("amount_credits", Integer),
    Column("related_message_id", Uuid),
    Column("description", String),
    Column("created_at", DateTime(timezone=True), server_default=text("now()")),
)
```

- [ ] **Step 2: Adicionar `Boolean` ao import do topo do arquivo**

Trocar:

```python
from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, String, Table, Text, Uuid, text
```

por:

```python
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    Uuid,
    text,
)
```

- [ ] **Step 3: Verificar que nada quebrou**

Run: `cd apps/worker && uv run pytest tests/unit -q && uv run ruff check .`
Expected: todos os testes passam.

- [ ] **Step 4: Commit**

```bash
git add apps/worker/app/tables.py
git commit -m "feat(worker): tabelas da cobrança do cliente final"
```

---

### Task 2: `worker` — `send_message_to_agents` propaga `end_customer_billing`

**Files:**
- Modify: `apps/worker/app/clients/agents.py`
- Test: `apps/worker/tests/unit/test_agents_client.py`

**Interfaces:**
- Produces: `send_message_to_agents(..., end_customer_billing: dict | None = None)` — o campo só entra no JSON quando não é `None`; consumida pela Task 4.

- [ ] **Step 1: Escrever o teste**

Adicionar ao final de `apps/worker/tests/unit/test_agents_client.py`:

```python
async def test_inclui_end_customer_billing_quando_presente() -> None:
    response = MagicMock(spec=Response, status_code=200)
    response.json.return_value = {"responses": ["oi"], "tokens_used": 100}
    http = _http_returning(response)
    billing = {"enabled": True, "balance": 0, "packages": [{"id": "p-1", "name": "Básico"}]}

    await send_message_to_agents(http, **KWARGS, end_customer_billing=billing)

    body = http.post.await_args.kwargs["json"]
    assert body["end_customer_billing"] == billing


async def test_omite_end_customer_billing_quando_none() -> None:
    response = MagicMock(spec=Response, status_code=200)
    response.json.return_value = {"responses": ["oi"], "tokens_used": 100}
    http = _http_returning(response)

    await send_message_to_agents(http, **KWARGS)

    body = http.post.await_args.kwargs["json"]
    assert "end_customer_billing" not in body
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `uv run pytest tests/unit/test_agents_client.py -v`
Expected: FAIL — `TypeError: send_message_to_agents() got an unexpected keyword argument 'end_customer_billing'`.

- [ ] **Step 3: Implementar**

Em `apps/worker/app/clients/agents.py`, trocar a assinatura e o corpo:

```python
async def send_message_to_agents(
    http: httpx.AsyncClient,
    *,
    tenant_id: str,
    contact_phone_number: str,
    message: str,
    phone_number_id: str,
    access_token: str,
    end_customer_billing: dict | None = None,
) -> dict | None:
    """Chama POST /messages do agents service.

    `end_customer_billing` (quando não None) leva {"enabled", "balance",
    "packages"} do cliente final — nenhum dado sensível, a secret key da
    Stripe do tenant nunca sai do api.
    """
    headers = {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}
    payload = {
        "tenant_id": tenant_id,
        "contact_phone_number": contact_phone_number,
        "message": message,
        "attachments": [],
        "phone_number_id": phone_number_id,
        "access_token": access_token,
    }
    if end_customer_billing is not None:
        payload["end_customer_billing"] = end_customer_billing

    response = await http.post("/messages", json=payload, headers=headers)
    if response.status_code == 202:
        return None
    response.raise_for_status()
    data = response.json()
    return {
        "responses": data.get("responses", []),
        "tokens_used": data.get("tokens_used", 0),
        "delivery_failures": data.get("delivery_failures", []),
    }
```

- [ ] **Step 4: Rodar de novo**

Run: `uv run pytest tests/unit/test_agents_client.py -v`
Expected: PASS (todos os testes, incluindo os 2 novos).

- [ ] **Step 5: Commit**

```bash
git add apps/worker/app/clients/agents.py apps/worker/tests/unit/test_agents_client.py
git commit -m "feat(worker): propaga saldo/pacotes do cliente final pro agents"
```

---

### Task 3: `worker` — `_load_context` lê saldo/pacotes do cliente final

**Files:**
- Modify: `apps/worker/app/tasks/messages.py`
- Test: `apps/worker/tests/unit/test_load_context.py`

**Interfaces:**
- Consumes: `tables.tenant_billing_settings`, `tables.end_customer_balances`, `tables.end_customer_credit_packages` (Task 1).
- Produces: `InboundContext` ganha `end_customer_billing_enabled: bool`, `end_customer_tokens_per_credit: int | None`, `end_customer_balance: int`, `end_customer_packages: list[dict]` — consumidos pela Task 4.

- [ ] **Step 1: Escrever o teste**

```python
# apps/worker/tests/unit/test_load_context.py
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.tasks.messages import _load_context

TENANT_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())
MESSAGE_ID = str(uuid.uuid4())


def _session_with(conversation, content, number, credit_balance, billing_settings, balance, packages):
    session = AsyncMock()

    def _result(value=None, scalar=None, rows=None):
        result = MagicMock()
        result.one_or_none.return_value = value
        result.scalar_one_or_none.return_value = scalar
        result.scalar_one.return_value = scalar
        result.__iter__ = lambda self: iter(rows or [])
        return result

    session.execute = AsyncMock(
        side_effect=[
            _result(value=conversation),
            _result(scalar=content),
            _result(value=number),
            _result(scalar=credit_balance),
            _result(value=billing_settings),
            _result(scalar=balance),
            _result(rows=packages),
        ]
    )
    return session


def _conversation():
    return SimpleNamespace(state="agent", contact_phone_number="5511999998888")


def _number():
    return SimpleNamespace(phone_number_id="PNID", access_token_encrypted="cifrado")


async def test_billing_desabilitado_retorna_saldo_zero_e_sem_pacotes() -> None:
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=None,
        balance=None,
        packages=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.end_customer_billing_enabled is False
    assert context.end_customer_balance == 0
    assert context.end_customer_packages == []


async def test_billing_habilitado_le_saldo_e_pacotes() -> None:
    billing_settings = SimpleNamespace(enabled=True, end_customer_tokens_per_credit=500)
    package_row = SimpleNamespace(
        id=uuid.uuid4(), name="Básico", price_brl=49.9, credits_granted=500
    )
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=billing_settings,
        balance=250,
        packages=[package_row],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.end_customer_billing_enabled is True
    assert context.end_customer_tokens_per_credit == 500
    assert context.end_customer_balance == 250
    assert context.end_customer_packages == [
        {
            "id": str(package_row.id),
            "name": "Básico",
            "price_brl": "49.9",
            "credits_granted": 500,
        }
    ]


async def test_billing_habilitado_sem_saldo_ainda_usa_zero() -> None:
    billing_settings = SimpleNamespace(enabled=True, end_customer_tokens_per_credit=500)
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=billing_settings,
        balance=None,
        packages=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.end_customer_balance == 0
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `uv run pytest tests/unit/test_load_context.py -v`
Expected: FAIL — `AttributeError: 'InboundContext' object has no attribute 'end_customer_billing_enabled'`.

- [ ] **Step 3: Implementar**

Em `apps/worker/app/tasks/messages.py`, estender o dataclass:

```python
@dataclass
class InboundContext:
    conversation_state: str
    contact_phone_number: str
    message_content: str
    phone_number_id: str
    access_token_encrypted: str
    credit_balance: int
    end_customer_billing_enabled: bool
    end_customer_tokens_per_credit: int | None
    end_customer_balance: int
    end_customer_packages: list[dict]
```

E estender `_load_context` (troca o `return InboundContext(...)` final e adiciona as novas queries antes dele):

```python
    billing_settings = (
        await session.execute(
            select(
                tables.tenant_billing_settings.c.enabled,
                tables.tenant_billing_settings.c.end_customer_tokens_per_credit,
            ).where(tables.tenant_billing_settings.c.tenant_id == uuid.UUID(tenant_id))
        )
    ).one_or_none()

    end_customer_billing_enabled = bool(billing_settings and billing_settings.enabled)
    end_customer_tokens_per_credit = (
        billing_settings.end_customer_tokens_per_credit if billing_settings else None
    )
    end_customer_balance = 0
    end_customer_packages: list[dict] = []

    if end_customer_billing_enabled:
        balance = (
            await session.execute(
                select(tables.end_customer_balances.c.credit_balance).where(
                    tables.end_customer_balances.c.tenant_id == uuid.UUID(tenant_id),
                    tables.end_customer_balances.c.contact_phone_number
                    == conversation.contact_phone_number,
                )
            )
        ).scalar_one_or_none()
        end_customer_balance = balance or 0

        packages_result = await session.execute(
            select(
                tables.end_customer_credit_packages.c.id,
                tables.end_customer_credit_packages.c.name,
                tables.end_customer_credit_packages.c.price_brl,
                tables.end_customer_credit_packages.c.credits_granted,
            ).where(
                tables.end_customer_credit_packages.c.tenant_id == uuid.UUID(tenant_id),
                tables.end_customer_credit_packages.c.active.is_(True),
            )
        )
        end_customer_packages = [
            {
                "id": str(row.id),
                "name": row.name,
                "price_brl": str(row.price_brl),
                "credits_granted": row.credits_granted,
            }
            for row in packages_result
        ]

    return InboundContext(
        conversation_state=conversation.state,
        contact_phone_number=conversation.contact_phone_number,
        message_content=content,
        phone_number_id=number.phone_number_id,
        access_token_encrypted=number.access_token_encrypted,
        credit_balance=credit_balance,
        end_customer_billing_enabled=end_customer_billing_enabled,
        end_customer_tokens_per_credit=end_customer_tokens_per_credit,
        end_customer_balance=end_customer_balance,
        end_customer_packages=end_customer_packages,
    )
```

- [ ] **Step 4: Rodar de novo**

Run: `uv run pytest tests/unit/test_load_context.py -v`
Expected: PASS (todos os 3 testes).

- [ ] **Step 5: Rodar a suíte completa (garantir que `process_inbound_message` — que mocka `_load_context` — não quebrou)**

Run: `uv run pytest tests/unit -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_load_context.py
git commit -m "feat(worker): _load_context lê saldo/pacotes do cliente final"
```

---

### Task 4: `worker` — monta o payload pro `agents` e debita o cliente final

**Files:**
- Modify: `apps/worker/app/tasks/messages.py`
- Test: `apps/worker/tests/unit/test_process_inbound_message.py`
- Test: `apps/worker/tests/unit/test_debitar_creditos_cliente_final.py`

**Interfaces:**
- Consumes: `InboundContext` (Task 3), `send_message_to_agents(..., end_customer_billing=...)` (Task 2), `tables.end_customer_credit_transactions`/`tables.end_customer_balances` (Task 1).
- Produces: `_debitar_creditos_cliente_final(session, tenant_id, contact_phone_number, message_id, tokens_used, credits) -> None`.

- [ ] **Step 1: Escrever o teste de `_debitar_creditos_cliente_final` (mesmo padrão `FakeSession` de `test_persist_agent_responses.py`)**

```python
# apps/worker/tests/unit/test_debitar_creditos_cliente_final.py
import uuid

from app.tasks import messages as messages_task

TENANT_ID = str(uuid.uuid4())
CONTACT = "5511999998888"
MESSAGE_ID = uuid.uuid4()


class FakeSession:
    def __init__(self):
        self.executed: list[dict] = []

    async def execute(self, stmt):
        params = dict(stmt.compile().params)
        self.executed.append(params)


async def test_lanca_consumption_negativo_e_atualiza_saldo() -> None:
    session = FakeSession()

    await messages_task._debitar_creditos_cliente_final(
        session, TENANT_ID, CONTACT, MESSAGE_ID, tokens_used=2000, credits=4
    )

    transaction = session.executed[0]
    assert transaction["type"] == "consumption"
    assert transaction["amount_credits"] == -4
    assert transaction["contact_phone_number"] == CONTACT
    assert transaction["related_message_id"] == MESSAGE_ID
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `uv run pytest tests/unit/test_debitar_creditos_cliente_final.py -v`
Expected: FAIL — `AttributeError: module 'app.tasks.messages' has no attribute '_debitar_creditos_cliente_final'`.

- [ ] **Step 3: Escrever o teste de integração no fluxo `process_inbound_message`**

Adicionar ao final de `apps/worker/tests/unit/test_process_inbound_message.py`:

```python
def _inbound_com_billing(balance: int, tokens_per_credit: int = 500) -> InboundContext:
    return InboundContext(
        conversation_state="agent",
        contact_phone_number="5511888888888",
        message_content="Olá",
        phone_number_id="PNID",
        access_token_encrypted="token-cifrado",
        credit_balance=1000,
        end_customer_billing_enabled=True,
        end_customer_tokens_per_credit=tokens_per_credit,
        end_customer_balance=balance,
        end_customer_packages=[{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
    )


async def test_billing_habilitado_com_saldo_debita_cliente_final(monkeypatch) -> None:
    mocks = {
        "load": AsyncMock(return_value=_inbound_com_billing(balance=1000)),
        "decrypt": MagicMock(return_value="token-claro"),
        "send": AsyncMock(return_value={"responses": ["oi"], "tokens_used": 2000}),
        "persist": AsyncMock(return_value=FIRST_MESSAGE_ID),
        "debitar": AsyncMock(),
        "debitar_cliente_final": AsyncMock(),
    }
    monkeypatch.setattr(messages_task, "_load_context", mocks["load"])
    monkeypatch.setattr(messages_task, "decrypt_access_token", mocks["decrypt"])
    monkeypatch.setattr(messages_task, "send_message_to_agents", mocks["send"])
    monkeypatch.setattr(messages_task, "_persist_agent_responses", mocks["persist"])
    monkeypatch.setattr(messages_task, "_debitar_creditos", mocks["debitar"])
    monkeypatch.setattr(
        messages_task, "_debitar_creditos_cliente_final", mocks["debitar_cliente_final"]
    )

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    mocks["send"].assert_awaited_once()
    assert mocks["send"].await_args.kwargs["end_customer_billing"]["balance"] == 1000
    mocks["debitar_cliente_final"].assert_awaited_once()
    assert mocks["debitar_cliente_final"].await_args.args[4] == 2000  # tokens_used
    assert mocks["debitar_cliente_final"].await_args.args[5] == 4  # ceil(2000/500)


async def test_billing_habilitado_sem_saldo_nao_debita_cliente_final(monkeypatch) -> None:
    mocks = {
        "load": AsyncMock(return_value=_inbound_com_billing(balance=0)),
        "decrypt": MagicMock(return_value="token-claro"),
        "send": AsyncMock(return_value={"responses": ["oi"], "tokens_used": 2000}),
        "persist": AsyncMock(return_value=FIRST_MESSAGE_ID),
        "debitar": AsyncMock(),
        "debitar_cliente_final": AsyncMock(),
    }
    monkeypatch.setattr(messages_task, "_load_context", mocks["load"])
    monkeypatch.setattr(messages_task, "decrypt_access_token", mocks["decrypt"])
    monkeypatch.setattr(messages_task, "send_message_to_agents", mocks["send"])
    monkeypatch.setattr(messages_task, "_persist_agent_responses", mocks["persist"])
    monkeypatch.setattr(messages_task, "_debitar_creditos", mocks["debitar"])
    monkeypatch.setattr(
        messages_task, "_debitar_creditos_cliente_final", mocks["debitar_cliente_final"]
    )

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    mocks["send"].assert_awaited_once()
    assert mocks["send"].await_args.kwargs["end_customer_billing"]["balance"] == 0
    mocks["debitar_cliente_final"].assert_not_awaited()


async def test_billing_desabilitado_nao_manda_bloco_e_nao_debita(monkeypatch) -> None:
    mocks = {
        "load": AsyncMock(return_value=_inbound()),  # helper já existente, sem billing habilitado
        "decrypt": MagicMock(return_value="token-claro"),
        "send": AsyncMock(return_value={"responses": ["oi"], "tokens_used": 2000}),
        "persist": AsyncMock(return_value=FIRST_MESSAGE_ID),
        "debitar": AsyncMock(),
        "debitar_cliente_final": AsyncMock(),
    }
    monkeypatch.setattr(messages_task, "_load_context", mocks["load"])
    monkeypatch.setattr(messages_task, "decrypt_access_token", mocks["decrypt"])
    monkeypatch.setattr(messages_task, "send_message_to_agents", mocks["send"])
    monkeypatch.setattr(messages_task, "_persist_agent_responses", mocks["persist"])
    monkeypatch.setattr(messages_task, "_debitar_creditos", mocks["debitar"])
    monkeypatch.setattr(
        messages_task, "_debitar_creditos_cliente_final", mocks["debitar_cliente_final"]
    )

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert "end_customer_billing" not in mocks["send"].await_args.kwargs
    mocks["debitar_cliente_final"].assert_not_awaited()
```

Ajustar a fixture `_inbound` já existente no topo do arquivo (Task 3 estendeu `InboundContext` com campos obrigatórios — sem valor default, toda instanciação precisa deles):

```python
def _inbound(state: str = "agent", credit_balance: int = 1000) -> InboundContext:
    return InboundContext(
        conversation_state=state,
        contact_phone_number="5511888888888",
        message_content="Olá",
        phone_number_id="PNID",
        access_token_encrypted="token-cifrado",
        credit_balance=credit_balance,
        end_customer_billing_enabled=False,
        end_customer_tokens_per_credit=None,
        end_customer_balance=0,
        end_customer_packages=[],
    )
```

- [ ] **Step 4: Rodar e confirmar que os testes novos falham**

Run: `uv run pytest tests/unit/test_process_inbound_message.py -v`
Expected: FAIL nos 3 testes novos (`_debitar_creditos_cliente_final` inexistente / `send_message_to_agents` sem `end_customer_billing`).

- [ ] **Step 5: Implementar `_debitar_creditos_cliente_final` e o encaixe em `process_inbound_message`**

Adicionar ao final de `apps/worker/app/tasks/messages.py`:

```python
async def _debitar_creditos_cliente_final(
    session: AsyncSession,
    tenant_id: str,
    contact_phone_number: str,
    message_id: uuid.UUID,
    tokens_used: int,
    credits: int,
) -> None:
    """Débito do saldo do CLIENTE FINAL com o tenant — independente do débito
    do tenant com a plataforma (_debitar_creditos), mesma transação."""
    await session.execute(
        insert(tables.end_customer_credit_transactions).values(
            tenant_id=uuid.UUID(tenant_id),
            contact_phone_number=contact_phone_number,
            type="consumption",
            amount_credits=-credits,
            related_message_id=message_id,
            description=f"Consumo do agente ({tokens_used} tokens)",
            created_at=datetime.now(UTC),
        )
    )
    await session.execute(
        update(tables.end_customer_balances)
        .where(
            tables.end_customer_balances.c.tenant_id == uuid.UUID(tenant_id),
            tables.end_customer_balances.c.contact_phone_number == contact_phone_number,
        )
        .values(credit_balance=tables.end_customer_balances.c.credit_balance - credits)
    )
```

Em `process_inbound_message`, montar o bloco de billing **antes** da chamada a `send_message_to_agents` e trocar a chamada:

```python
    end_customer_billing = None
    if inbound.end_customer_billing_enabled:
        end_customer_billing = {
            "enabled": True,
            "balance": inbound.end_customer_balance,
            "packages": inbound.end_customer_packages,
        }

    try:
        result = await send_message_to_agents(
            http,
            tenant_id=tenant_id,
            contact_phone_number=inbound.contact_phone_number,
            message=inbound.message_content,
            phone_number_id=inbound.phone_number_id,
            access_token=access_token,
            end_customer_billing=end_customer_billing,
        )
```

E, no bloco final (depois de `_persist_agent_responses`, antes do `await session.commit()`), adicionar o débito do cliente final:

```python
    async with open_tenant_session(session_factory, tenant_id) as session:
        first_message_id = await _persist_agent_responses(
            session, tenant_id, conversation_id, responses, tokens_used, credits, delivery_failures
        )
        if credits and first_message_id is not None:
            await _debitar_creditos(session, tenant_id, first_message_id, tokens_used, credits)

        if (
            inbound.end_customer_billing_enabled
            and inbound.end_customer_balance > 0
            and tokens_used
            and inbound.end_customer_tokens_per_credit
            and first_message_id is not None
        ):
            end_customer_credits = math.ceil(tokens_used / inbound.end_customer_tokens_per_credit)
            if end_customer_credits:
                await _debitar_creditos_cliente_final(
                    session,
                    tenant_id,
                    inbound.contact_phone_number,
                    first_message_id,
                    tokens_used,
                    end_customer_credits,
                )

        await session.commit()
```

- [ ] **Step 6: Rodar os testes de novo**

Run: `uv run pytest tests/unit/test_process_inbound_message.py tests/unit/test_debitar_creditos_cliente_final.py -v`
Expected: PASS (todos, incluindo os pré-existentes).

- [ ] **Step 7: Rodar a suíte completa + lint**

Run: `uv run pytest tests/unit -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_process_inbound_message.py apps/worker/tests/unit/test_debitar_creditos_cliente_final.py
git commit -m "feat(worker): débito do saldo do cliente final na mesma transação"
```

---

### Task 5: `agents` — contrato `POST /messages`/`run_agent`/`State` propagam `end_customer_billing`

**Files:**
- Modify: `apps/agents/api/routes.py`
- Modify: `apps/agents/services/call_agent.py`
- Modify: `apps/agents/agents/workflow.py`
- Test: `apps/agents/tests/unit/test_routes.py`

**Interfaces:**
- Produces: `IncomingMessage.end_customer_billing: dict | None`; `run_agent(..., end_customer_billing: dict | None = None)`; `State.end_customer_billing: dict | None` — consumidos pelas Tasks 6-8 (o grafo lê `state["end_customer_billing"]`).

- [ ] **Step 1: Escrever o teste**

Adicionar ao final de `apps/agents/tests/unit/test_routes.py`:

```python
def test_end_customer_billing_e_repassado_ao_run_agent(client, monkeypatch):
    debounce = AsyncMock(return_value={"combined_message": "olá", "other_exec_is_running": False})
    run_agent = AsyncMock(return_value=(["oi"], 100, "agente_secretaria"))
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    _mock_whatsapp_client(monkeypatch)

    billing = {"enabled": True, "balance": 0, "packages": [{"id": "p-1", "name": "Básico"}]}
    payload = {**PAYLOAD, "end_customer_billing": billing}

    response = client.post("/messages", json=payload)

    assert response.status_code == 200
    assert run_agent.call_args.kwargs["end_customer_billing"] == billing


def test_sem_end_customer_billing_repassa_none(client, monkeypatch):
    debounce = AsyncMock(return_value={"combined_message": "olá", "other_exec_is_running": False})
    run_agent = AsyncMock(return_value=(["oi"], 100, "agente_secretaria"))
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    _mock_whatsapp_client(monkeypatch)

    response = client.post("/messages", json=PAYLOAD)

    assert response.status_code == 200
    assert run_agent.call_args.kwargs["end_customer_billing"] is None
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd apps/agents && uv run pytest tests/unit/test_routes.py -v`
Expected: FAIL — `AssertionError` (campo ausente do `call_args.kwargs`, `run_agent` chamado sem `end_customer_billing`).

- [ ] **Step 3: Implementar**

Em `apps/agents/api/routes.py`, adicionar o campo à `IncomingMessage`:

```python
    end_customer_billing: dict | None = None
```

E passar pra `run_agent`:

```python
        response, tokens_used, current_agent = await run_agent(
            message=messages["combined_message"],
            attachments=body.attachments,
            conversation_id=thread_id,
            number_whatsapp=body.contact_phone_number,
            end_customer_billing=body.end_customer_billing,
        )
```

Em `apps/agents/services/call_agent.py`, adicionar o parâmetro e propagar no `ainvoke`:

```python
async def run_agent(
    message: str,
    conversation_id: str,
    attachments: list = [],
    number_whatsapp: str | None = None,
    db_uri: str = DB_URI,
    num_before_messages: int = 35,
    extra_data: dict = {},
    end_customer_billing: dict | None = None,
) -> tuple[list[str], int, str]:
```

```python
        response = await agent.ainvoke(
            {
                "messages": [HumanMessage(content=message)],
                "attachments": attachments,
                "conversation_id": conversation_id,
                "num_before_messages": num_before_messages,
                "end_customer_billing": end_customer_billing,
            },
            config=config,
        )
```

Em `apps/agents/agents/workflow.py`, adicionar o campo ao `State`:

```python
class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    attachments: list
    conversation_id: str
    num_before_messages: int
    current_specialist: str | None = None
    receptive_message_specialist: bool = False
    end_customer_billing: dict | None = None
```

- [ ] **Step 4: Rodar de novo**

Run: `uv run pytest tests/unit/test_routes.py -v`
Expected: PASS (todos os testes, incluindo os 2 novos).

- [ ] **Step 5: Rodar a suíte completa**

Run: `uv run pytest tests/unit -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/agents/api/routes.py apps/agents/services/call_agent.py apps/agents/agents/workflow.py apps/agents/tests/unit/test_routes.py
git commit -m "feat(agents): contrato POST /messages propaga end_customer_billing"
```

---

### Task 6: `agents` — client `criar_link_pagamento` + tool `gerar_link_pagamento_cliente`

**Files:**
- Create: `apps/agents/clients/billing.py`
- Modify: `apps/agents/agents/tools.py`
- Test: `apps/agents/tests/unit/test_billing_client.py`
- Test: `apps/agents/tests/unit/test_tools.py`

**Interfaces:**
- Produces: `criar_link_pagamento(tenant_id, contact_phone_number, package_id) -> str | None`; tool `gerar_link_pagamento_cliente(package_id, conversation_id) -> str` — consumida pela Task 8 (bindada à secretária).

- [ ] **Step 1: Escrever o teste do client**

```python
# apps/agents/tests/unit/test_billing_client.py
from unittest.mock import MagicMock

import pytest

import clients.billing as billing_module
from clients.billing import criar_link_pagamento


class FakeAsyncClient:
    calls: list

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        FakeAsyncClient.calls.append((url, kwargs))
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"checkout_url": "https://checkout.stripe.com/pay/cs_1"}
        return response


@pytest.fixture(autouse=True)
def fake_httpx(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr(billing_module.httpx, "AsyncClient", FakeAsyncClient)


async def test_sucesso_retorna_checkout_url():
    url = await criar_link_pagamento("tenant-1", "5511999998888", "pkg-1")

    assert url == "https://checkout.stripe.com/pay/cs_1"
    (_, kwargs) = FakeAsyncClient.calls[0]
    assert kwargs["json"] == {
        "tenant_id": "tenant-1",
        "contact_phone_number": "5511999998888",
        "package_id": "pkg-1",
    }


async def test_falha_http_retorna_none(monkeypatch):
    class FailingClient(FakeAsyncClient):
        async def post(self, url, **kwargs):
            import httpx

            request = httpx.Request("POST", url)
            response = httpx.Response(502, request=request)
            raise httpx.HTTPStatusError("erro", request=request, response=response)

    monkeypatch.setattr(billing_module.httpx, "AsyncClient", FailingClient)

    assert await criar_link_pagamento("tenant-1", "5511999998888", "pkg-1") is None
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd apps/agents && uv run pytest tests/unit/test_billing_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'clients.billing'`.

- [ ] **Step 3: Implementar o client**

```python
# apps/agents/clients/billing.py
"""Cria o link de pagamento chamando o endpoint interno do api — a secret
key da Stripe do tenant nunca chega até o agents, só a URL final."""

import os

import httpx
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

API_URL = os.getenv("API_URL", "http://api:8000")
INTERNAL_SERVICE_KEY = os.getenv("INTERNAL_SERVICE_KEY", "")


async def criar_link_pagamento(
    tenant_id: str, contact_phone_number: str, package_id: str
) -> str | None:
    headers = {"Authorization": INTERNAL_SERVICE_KEY} if INTERNAL_SERVICE_KEY else {}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{API_URL}/api/v1/internal/end-customer-billing/checkout",
                json={
                    "tenant_id": tenant_id,
                    "contact_phone_number": contact_phone_number,
                    "package_id": package_id,
                },
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()["checkout_url"]
    except httpx.HTTPStatusError as e:
        logger.error(
            "Erro HTTP ao criar link de pagamento | status={} | response={}",
            e.response.status_code,
            e.response.text,
        )
        return None
    except Exception as e:
        logger.error("Erro ao criar link de pagamento | error={}", str(e))
        return None
```

- [ ] **Step 4: Rodar o teste do client de novo**

Run: `uv run pytest tests/unit/test_billing_client.py -v`
Expected: PASS.

- [ ] **Step 5: Escrever o teste da tool**

Adicionar ao final de `apps/agents/tests/unit/test_tools.py` (o `from agents.tools import (...)` do topo do arquivo ganha `gerar_link_pagamento_cliente`):

```python
@pytest.mark.asyncio
async def test_gerar_link_pagamento_divide_conversation_id_e_retorna_url():
    with patch(
        "agents.tools.criar_link_pagamento", new=AsyncMock(return_value="https://checkout.stripe.com/pay/cs_1")
    ) as mock_fn:
        result = await gerar_link_pagamento_cliente.ainvoke(
            {"package_id": "pkg-1", "conversation_id": "tenant-1:5511999998888"}
        )

        mock_fn.assert_called_once_with("tenant-1", "5511999998888", "pkg-1")
        assert "https://checkout.stripe.com/pay/cs_1" in result


@pytest.mark.asyncio
async def test_gerar_link_pagamento_falha_retorna_mensagem_amigavel():
    with patch("agents.tools.criar_link_pagamento", new=AsyncMock(return_value=None)):
        result = await gerar_link_pagamento_cliente.ainvoke(
            {"package_id": "pkg-1", "conversation_id": "tenant-1:5511999998888"}
        )

        assert "não foi possível" in result.lower()
```

- [ ] **Step 6: Rodar e confirmar que falha**

Run: `uv run pytest tests/unit/test_tools.py -v`
Expected: FAIL — `ImportError: cannot import name 'gerar_link_pagamento_cliente'`.

- [ ] **Step 7: Implementar a tool em `apps/agents/agents/tools.py`**

Adicionar o import no topo:

```python
from clients.billing import criar_link_pagamento
```

Adicionar a tool (antes da lista `tools` no final do arquivo):

```python
@tool("gerar_link_pagamento_cliente")
async def gerar_link_pagamento_cliente(package_id: str, conversation_id: str) -> str:
    """Gera o link de pagamento (Stripe) pro cliente comprar um pacote de créditos.

    Use quando o cliente não tiver saldo suficiente pra continuar sendo
    atendido por um especialista, ou quando ele pedir explicitamente pra
    comprar mais créditos. Escolha o package_id entre os pacotes informados
    no seu contexto — nunca invente um id.

    Args:
        package_id: id do pacote escolhido (vem da lista de pacotes disponíveis).
        conversation_id: preenchido automaticamente pelo sistema.
    """
    tenant_id, _, contact_phone_number = str(conversation_id).partition(":")
    checkout_url = await criar_link_pagamento(tenant_id, contact_phone_number, package_id)
    if checkout_url is None:
        return (
            "Não foi possível gerar o link de pagamento agora — peça pro cliente "
            "tentar de novo em instantes."
        )
    return f"Link de pagamento gerado: {checkout_url}"
```

Adicionar `gerar_link_pagamento_cliente` à lista `tools` no final do arquivo:

```python
tools = [
    bucar_base_conhecimento_condominial,
    bucar_base_conhecimento_contratos,
    bucar_base_conhecimento_direito_consumidor,
    bucar_base_conhecimento_usuario,
    buscar_base_conhecimento_escritorio,
    gerar_link_pagamento_cliente,
    transfer_to_specialist,
]
```

- [ ] **Step 8: Rodar o teste da tool de novo**

Run: `uv run pytest tests/unit/test_tools.py -v`
Expected: PASS (todos os testes, incluindo os 2 novos).

- [ ] **Step 9: Commit**

```bash
git add apps/agents/clients/billing.py apps/agents/agents/tools.py apps/agents/tests/unit/test_billing_client.py apps/agents/tests/unit/test_tools.py
git commit -m "feat(agents): tool gerar_link_pagamento_cliente"
```

---

### Task 7: `agents` — gate técnico em `transfer_to_specialist`

**Files:**
- Modify: `apps/agents/agents/tools.py`
- Modify: `apps/agents/agents/nodes.py`
- Test: `apps/agents/tests/unit/test_tools.py`
- Test: `apps/agents/tests/unit/test_nodes.py`

**Interfaces:**
- Consumes: `gerar_link_pagamento_cliente` já na lista `tools` (Task 6).
- Produces: `transfer_to_specialist` recusa a transferência (retorna `str` em vez de `Command`) quando `end_customer_billing_enabled=True` e `end_customer_balance<=0`; `tool_node` injeta esses dois argumentos do `state["end_customer_billing"]`, nunca do LLM — consumido pela Task 8 (a secretária precisa saber lidar com a recusa).

- [ ] **Step 1: Escrever os testes da tool**

Adicionar ao final da seção `transfer_to_specialist` em `apps/agents/tests/unit/test_tools.py`:

```python
def test_transfer_bloqueada_sem_saldo_retorna_string():
    result = transfer_to_specialist.invoke(
        {
            "current_specialist": "agente_condominial",
            "end_customer_billing_enabled": True,
            "end_customer_balance": 0,
        }
    )
    assert isinstance(result, str)
    assert "bloqueada" in result.lower()


def test_transfer_liberada_com_saldo_positivo():
    result = transfer_to_specialist.invoke(
        {
            "current_specialist": "agente_condominial",
            "end_customer_billing_enabled": True,
            "end_customer_balance": 100,
        }
    )
    assert isinstance(result, Command)
    assert result.update["current_specialist"] == "agente_condominial"


def test_transfer_sem_billing_habilitado_ignora_saldo():
    result = transfer_to_specialist.invoke(
        {
            "current_specialist": "agente_condominial",
            "end_customer_billing_enabled": False,
            "end_customer_balance": 0,
        }
    )
    assert isinstance(result, Command)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd apps/agents && uv run pytest tests/unit/test_tools.py -v`
Expected: FAIL — `test_transfer_bloqueada_sem_saldo_retorna_string` recebe `Command` em vez de recusa (a tool ainda não tem o parâmetro/gate).

- [ ] **Step 3: Implementar o gate na tool**

Em `apps/agents/agents/tools.py`, trocar `transfer_to_specialist` por:

```python
@tool("transfer_to_specialist")
def transfer_to_specialist(
    current_specialist: Literal["agente_condominial", "agente_contratos", "agente_direito_consumidor"],
    end_customer_billing_enabled: bool = False,
    end_customer_balance: int = 0,
) -> str:
    """
    Atualiza o estado do agente para transferir a conversa para um especialista.

    Args:
        current_specialist: Nome do especialista a ser transferido.
        end_customer_billing_enabled: preenchido automaticamente pelo sistema.
        end_customer_balance: preenchido automaticamente pelo sistema.
    """
    if end_customer_billing_enabled and end_customer_balance <= 0:
        return (
            "Transferência bloqueada: o cliente ainda não tem créditos disponíveis. "
            "Ofereça os pacotes de crédito e gere o link de pagamento antes de "
            "transferir para um especialista."
        )
    return Command(
        update={
            "current_specialist": current_specialist,
            "receptive_message_specialist": True,
        }
    )
```

- [ ] **Step 4: Rodar o teste da tool de novo**

Run: `uv run pytest tests/unit/test_tools.py -v`
Expected: PASS (todos, incluindo os 3 novos).

- [ ] **Step 5: Escrever o teste de injeção no `tool_node`**

Adicionar ao final da seção "tool_node — injeção de conversation_id do estado" em `apps/agents/tests/unit/test_nodes.py`:

```python
@pytest.mark.asyncio
async def test_tool_node_injeta_saldo_do_cliente_final_em_transfer_to_specialist() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "transfer_to_specialist",
                # O LLM tentou passar saldo positivo — deve ser ignorado.
                "args": {
                    "current_specialist": "agente_condominial",
                    "end_customer_balance": 9999,
                },
                "id": "call-1",
            }
        ],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-1:5511999998888",
        "end_customer_billing": {"enabled": True, "balance": 0, "packages": []},
    }

    result = await tool_node(state)

    assert "bloqueada" in result["messages"][0].content.lower()


@pytest.mark.asyncio
async def test_tool_node_sem_end_customer_billing_no_state_nao_bloqueia() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "transfer_to_specialist", "args": {"current_specialist": "agente_contratos"}, "id": "call-1"}
        ],
    )
    state = {"messages": [message], "conversation_id": "tenant-1:5511999998888"}

    result = await tool_node(state)

    assert result.get("current_specialist") == "agente_contratos"
```

- [ ] **Step 6: Rodar e confirmar que falha**

Run: `uv run pytest tests/unit/test_nodes.py -v`
Expected: FAIL — `test_tool_node_injeta_saldo_do_cliente_final_em_transfer_to_specialist` falha (saldo `9999` do LLM ainda não é sobrescrito, `tool_node` não injeta nada).

- [ ] **Step 7: Implementar a injeção em `apps/agents/agents/nodes.py`**

Trocar:

```python
STATE_SCOPED_TOOLS = {"bucar_base_conhecimento_usuario", "buscar_base_conhecimento_escritorio"}
```

por:

```python
STATE_SCOPED_TOOLS = {
    "bucar_base_conhecimento_usuario",
    "buscar_base_conhecimento_escritorio",
    "gerar_link_pagamento_cliente",
}
# Saldo/enabled do cliente final: nunca confiar em valor vindo do LLM.
BILLING_GATED_TOOLS = {"transfer_to_specialist"}
```

E no loop de `tool_node`, trocar:

```python
        args = dict(tool_call["args"])
        if tool_call["name"] in STATE_SCOPED_TOOLS:
            args["conversation_id"] = state["conversation_id"]
```

por:

```python
        args = dict(tool_call["args"])
        if tool_call["name"] in STATE_SCOPED_TOOLS:
            args["conversation_id"] = state["conversation_id"]
        if tool_call["name"] in BILLING_GATED_TOOLS:
            billing = state.get("end_customer_billing") or {}
            args["end_customer_billing_enabled"] = bool(billing.get("enabled"))
            args["end_customer_balance"] = billing.get("balance", 0)
```

- [ ] **Step 8: Rodar o teste de novo**

Run: `uv run pytest tests/unit/test_nodes.py -v`
Expected: PASS (todos, incluindo os 2 novos).

- [ ] **Step 9: Rodar a suíte completa + lint**

Run: `uv run pytest tests/unit -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add apps/agents/agents/tools.py apps/agents/agents/nodes.py apps/agents/tests/unit/test_tools.py apps/agents/tests/unit/test_nodes.py
git commit -m "feat(agents): gate técnico de saldo do cliente final em transfer_to_specialist"
```

---

### Task 8: `agents` — secretária oferece os pacotes quando bloqueado

**Files:**
- Modify: `apps/agents/agents/nodes.py`
- Test: `apps/agents/tests/unit/test_nodes.py`

**Interfaces:**
- Consumes: `state["end_customer_billing"]` (Task 5), `gerar_link_pagamento_cliente` (Task 6).
- Produces: `agente_secretaria` bindando a nova tool e injetando os pacotes no prompt quando `enabled=True` e `balance<=0`.

- [ ] **Step 1: Escrever o teste**

Adicionar na seção `agente_secretaria` de `apps/agents/tests/unit/test_nodes.py` (o arquivo já importa `mock_model`/`ai_response`/`base_state` de `tests.factories` — reaproveitar):

```python
@pytest.mark.asyncio
async def test_secretaria_bind_inclui_gerar_link_pagamento(monkeypatch) -> None:
    from agents.nodes import agente_secretaria

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agente_secretaria(base_state(end_customer_billing={"enabled": False, "balance": 0, "packages": []}))

    bound_tools = model.bind_tools.call_args.args[0]
    tool_names = {t.name for t in bound_tools}
    assert "gerar_link_pagamento_cliente" in tool_names


@pytest.mark.asyncio
async def test_secretaria_injeta_pacotes_no_prompt_quando_sem_saldo(monkeypatch) -> None:
    from agents.nodes import agente_secretaria

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": True,
            "balance": 0,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agente_secretaria(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" in prompt_arg.content
    assert "p-1" in prompt_arg.content


@pytest.mark.asyncio
async def test_secretaria_nao_injeta_pacotes_com_saldo_positivo(monkeypatch) -> None:
    from agents.nodes import agente_secretaria

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": True,
            "balance": 500,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agente_secretaria(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" not in prompt_arg.content
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd apps/agents && uv run pytest tests/unit/test_nodes.py -v`
Expected: FAIL — `gerar_link_pagamento_cliente` ainda não está bindado; prompt não recebe o bloco de pacotes.

- [ ] **Step 3: Implementar em `apps/agents/agents/nodes.py`**

Trocar o início de `agente_secretaria`:

```python
async def agente_secretaria(state: dict) -> dict:
    logger.info(
        "agente_secretaria chamado | mensagens={} | histórico={}",
        len(state["messages"]),
        state["num_before_messages"],
    )

    last_messages = strip_messages(state["messages"], state["num_before_messages"])
    model_with_tools = model.bind_tools(
        [transfer_to_specialist, buscar_base_conhecimento_escritorio, gerar_link_pagamento_cliente]
    )

    with open("agents/prompts/secretaria.md", "r", encoding="utf-8") as arquivo:
        prompt = arquivo.read()

    billing = state.get("end_customer_billing") or {}
    if billing.get("enabled") and billing.get("balance", 0) <= 0:
        packages_text = "\n".join(
            f"- {p['name']}: R$ {p['price_brl']} = {p['credits_granted']} créditos "
            f"(package_id: {p['id']})"
            for p in billing.get("packages", [])
        )
        prompt += (
            "\n\n---\n"
            "**Instrução:** Este cliente está sem créditos disponíveis. Antes de "
            "transferir para um especialista, explique que é necessário comprar "
            "créditos e ofereça os pacotes abaixo. Quando o cliente escolher um, "
            "use a tool gerar_link_pagamento_cliente com o package_id correspondente.\n\n"
            f"Pacotes disponíveis:\n{packages_text}"
        )

    response = await model_with_tools.ainvoke([
        SystemMessage(content=prompt),
        *last_messages,
    ])
```

(o restante da função continua igual — não precisa tocar.)

- [ ] **Step 4: Rodar o teste de novo**

Run: `uv run pytest tests/unit/test_nodes.py -v`
Expected: PASS (todos, incluindo os 3 novos).

- [ ] **Step 5: Rodar a suíte completa + lint**

Run: `uv run pytest tests/unit -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/agents/agents/nodes.py apps/agents/tests/unit/test_nodes.py
git commit -m "feat(agents): secretária oferece pacotes de crédito quando o cliente está sem saldo"
```

---

### Task 9: `web` — painel de configuração da cobrança (settings)

**Files:**
- Modify: `apps/web/src/lib/backend.ts`
- Create: `apps/web/src/components/EndCustomerBillingPanel.tsx`
- Create: `apps/web/src/app/configuracoes/cobranca-clientes/page.tsx`
- Test: `apps/web/__tests__/EndCustomerBillingPanel.test.tsx`

**Interfaces:**
- Consumes: `GET/PATCH /api/v1/end-customer-billing/settings` (Plano 1, Task 5).
- Produces: `EndCustomerBillingPanel` — a Task 10 estende o mesmo componente com o CRUD de pacotes.

- [ ] **Step 1: Adicionar o prefixo ao proxy**

Em `apps/web/src/lib/backend.ts`, adicionar `"end-customer-billing"` a `ALLOWED_PREFIXES`:

```typescript
const ALLOWED_PREFIXES = [
  "conversations",
  "knowledge-base",
  "whatsapp",
  "signup",
  "billing",
  "dashboard",
  "profile",
  "end-customer-billing",
];
```

- [ ] **Step 2: Escrever o teste do painel (parte settings)**

```typescript
// apps/web/__tests__/EndCustomerBillingPanel.test.tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EndCustomerBillingPanel } from "@/components/EndCustomerBillingPanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

function mockLoad(settings: unknown, packages: unknown[] = []) {
  mockedFetch.mockImplementation(async (path: string) => {
    if (path === "end-customer-billing/settings") {
      return { ok: true, json: async () => settings };
    }
    if (path === "end-customer-billing/packages") {
      return { ok: true, json: async () => packages };
    }
    return { ok: false, json: async () => null };
  });
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("EndCustomerBillingPanel", () => {
  it("mostra o toggle desligado e sem secrets configuradas por padrão", async () => {
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      stripe_secret_key_configured: false,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByLabelText(/cobrar meus clientes/i)).not.toBeChecked());
    expect(screen.getByText(/secret key/i)).toBeInTheDocument();
  });

  it("envia PATCH com a secret key digitada", async () => {
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      stripe_secret_key_configured: false,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);
    await waitFor(() => expect(screen.getByLabelText(/secret key/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/secret key/i), { target: { value: "sk_test_123" } });
    fireEvent.click(screen.getByRole("button", { name: /salvar configuração/i }));

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith(
        "end-customer-billing/settings",
        expect.objectContaining({ method: "PATCH" }),
      ),
    );
    const call = mockedFetch.mock.calls.find(([path]) => path === "end-customer-billing/settings" && arguments);
    const patchCall = mockedFetch.mock.calls.find(
      ([path, init]) => path === "end-customer-billing/settings" && init?.method === "PATCH",
    );
    const body = JSON.parse(patchCall![1].body as string);
    expect(body.stripe_secret_key).toBe("sk_test_123");
  });

  it("mostra erro quando o PATCH falha (ex: habilitar sem secret key)", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "end-customer-billing/settings" && init?.method === "PATCH") {
        return { ok: false, json: async () => ({ detail: "Configure a secret key da Stripe antes de ativar" }) };
      }
      if (path === "end-customer-billing/settings") {
        return {
          ok: true,
          json: async () => ({
            enabled: false,
            billing_mode: "credits",
            stripe_secret_key_configured: false,
            stripe_webhook_secret_configured: false,
            end_customer_tokens_per_credit: null,
          }),
        };
      }
      return { ok: true, json: async () => [] };
    });

    render(<EndCustomerBillingPanel />);
    await waitFor(() => expect(screen.getByLabelText(/cobrar meus clientes/i)).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText(/cobrar meus clientes/i));
    fireEvent.click(screen.getByRole("button", { name: /salvar configuração/i }));

    await waitFor(() =>
      expect(screen.getByText(/configure a secret key/i)).toBeInTheDocument(),
    );
  });
});
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `cd apps/web && pnpm vitest run EndCustomerBillingPanel`
Expected: FAIL — módulo `@/components/EndCustomerBillingPanel` não existe.

- [ ] **Step 4: Implementar o componente (parte settings — a Task 10 adiciona os pacotes no mesmo arquivo)**

```typescript
// apps/web/src/components/EndCustomerBillingPanel.tsx
"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { backendFetch } from "@/lib/client-api";

type Settings = {
  enabled: boolean;
  billing_mode: string;
  stripe_secret_key_configured: boolean;
  stripe_webhook_secret_configured: boolean;
  end_customer_tokens_per_credit: number | null;
};

const EMPTY_SETTINGS: Settings = {
  enabled: false,
  billing_mode: "credits",
  stripe_secret_key_configured: false,
  stripe_webhook_secret_configured: false,
  end_customer_tokens_per_credit: null,
};

function extractErrorDetail(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export function EndCustomerBillingPanel() {
  const [settings, setSettings] = useState<Settings>(EMPTY_SETTINGS);
  const [loaded, setLoaded] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [secretKey, setSecretKey] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [tokensPerCredit, setTokensPerCredit] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      const response = await backendFetch("end-customer-billing/settings");
      if (response.ok) {
        const body: Settings = await response.json();
        setSettings(body);
        setEnabled(body.enabled);
        setTokensPerCredit(body.end_customer_tokens_per_credit?.toString() ?? "");
      }
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setSaving(true);
    try {
      const body: Record<string, unknown> = { enabled };
      if (secretKey) body.stripe_secret_key = secretKey;
      if (webhookSecret) body.stripe_webhook_secret = webhookSecret;
      if (tokensPerCredit) body.end_customer_tokens_per_credit = Number(tokensPerCredit);

      const response = await backendFetch("end-customer-billing/settings", {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      const responseBody = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(responseBody, "Falha ao salvar — tente novamente."));
        return;
      }
      setSettings(responseBody);
      setSecretKey("");
      setWebhookSecret("");
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setSaving(false);
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
        <h1 className="font-display text-xl font-semibold text-ink">Cobrança dos clientes</h1>
        <p className="text-sm text-muted">
          Use a sua própria conta Stripe para vender créditos aos seus clientes finais.
        </p>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <form onSubmit={handleSubmit} className="flex max-w-md flex-col gap-4">
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
            />
            Cobrar meus clientes pelo uso dos agentes
          </label>
          <label className="flex flex-col gap-1 text-sm text-ink">
            Secret Key da Stripe {settings.stripe_secret_key_configured && "(configurada)"}
            <input
              type="password"
              value={secretKey}
              onChange={(event) => setSecretKey(event.target.value)}
              placeholder={settings.stripe_secret_key_configured ? "••••••••" : "sk_..."}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-ink">
            Webhook Secret {settings.stripe_webhook_secret_configured && "(configurado)"}
            <input
              type="password"
              value={webhookSecret}
              onChange={(event) => setWebhookSecret(event.target.value)}
              placeholder={settings.stripe_webhook_secret_configured ? "••••••••" : "whsec_..."}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-ink">
            Tokens por crédito
            <input
              type="number"
              min={1}
              value={tokensPerCredit}
              onChange={(event) => setTokensPerCredit(event.target.value)}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <button
            type="submit"
            disabled={saving}
            className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
          >
            {saving ? "Salvando..." : "Salvar configuração"}
          </button>
        </form>
      </div>
    </main>
  );
}
```

- [ ] **Step 5: Criar a página**

```typescript
// apps/web/src/app/configuracoes/cobranca-clientes/page.tsx
import { EndCustomerBillingPanel } from "@/components/EndCustomerBillingPanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default function ConfiguracoesCobrancaClientesPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="cobranca" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <EndCustomerBillingPanel />
      </div>
    </div>
  );
}
```

(o `active="cobranca"` referencia o item novo que a Task 11 adiciona ao `TenantNav` — sem essa Task, o TypeScript reclama do literal; rode as Tasks em ordem.)

- [ ] **Step 6: Rodar o teste de novo**

Run: `pnpm vitest run EndCustomerBillingPanel`
Expected: PASS (todos os 3 testes).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/lib/backend.ts apps/web/src/components/EndCustomerBillingPanel.tsx apps/web/src/app/configuracoes/cobranca-clientes/page.tsx apps/web/__tests__/EndCustomerBillingPanel.test.tsx
git commit -m "feat(web): painel de configuração da cobrança do cliente final"
```

---

### Task 10: `web` — CRUD de pacotes no mesmo painel

**Files:**
- Modify: `apps/web/src/components/EndCustomerBillingPanel.tsx`
- Modify: `apps/web/__tests__/EndCustomerBillingPanel.test.tsx`

**Interfaces:**
- Consumes: `GET/POST/PATCH/DELETE /api/v1/end-customer-billing/packages` (Plano 1, Task 6).

- [ ] **Step 1: Escrever os testes do CRUD de pacotes**

Adicionar ao final de `apps/web/__tests__/EndCustomerBillingPanel.test.tsx`:

```typescript
it("lista os pacotes já cadastrados", async () => {
  mockLoad(
    {
      enabled: true,
      billing_mode: "credits",
      stripe_secret_key_configured: true,
      stripe_webhook_secret_configured: true,
      end_customer_tokens_per_credit: 500,
    },
    [{ id: "p-1", name: "Básico", price_brl: "49.90", credits_granted: 500, active: true }],
  );

  render(<EndCustomerBillingPanel />);

  await waitFor(() => expect(screen.getByText("Básico")).toBeInTheDocument());
});

it("cria um pacote novo", async () => {
  mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "end-customer-billing/packages" && init?.method === "POST") {
      return {
        ok: true,
        json: async () => ({ id: "p-2", name: "Growth", price_brl: "99.90", credits_granted: 1000, active: true }),
      };
    }
    if (path === "end-customer-billing/settings") {
      return {
        ok: true,
        json: async () => ({
          enabled: true,
          billing_mode: "credits",
          stripe_secret_key_configured: true,
          stripe_webhook_secret_configured: true,
          end_customer_tokens_per_credit: 500,
        }),
      };
    }
    if (path === "end-customer-billing/packages") {
      return { ok: true, json: async () => [] };
    }
    return { ok: false, json: async () => null };
  });

  render(<EndCustomerBillingPanel />);
  await waitFor(() => expect(screen.getByLabelText(/nome do pacote/i)).toBeInTheDocument());

  fireEvent.change(screen.getByLabelText(/nome do pacote/i), { target: { value: "Growth" } });
  fireEvent.change(screen.getByLabelText(/preço/i), { target: { value: "99.90" } });
  fireEvent.change(screen.getByLabelText(/créditos/i), { target: { value: "1000" } });
  fireEvent.click(screen.getByRole("button", { name: /adicionar pacote/i }));

  await waitFor(() => expect(screen.getByText("Growth")).toBeInTheDocument());
});

it("exclui um pacote após confirmação", async () => {
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "end-customer-billing/packages/p-1" && init?.method === "DELETE") {
      return { ok: true, json: async () => null };
    }
    if (path === "end-customer-billing/settings") {
      return {
        ok: true,
        json: async () => ({
          enabled: true,
          billing_mode: "credits",
          stripe_secret_key_configured: true,
          stripe_webhook_secret_configured: true,
          end_customer_tokens_per_credit: 500,
        }),
      };
    }
    if (path === "end-customer-billing/packages") {
      return {
        ok: true,
        json: async () => [{ id: "p-1", name: "Básico", price_brl: "49.90", credits_granted: 500, active: true }],
      };
    }
    return { ok: false, json: async () => null };
  });

  render(<EndCustomerBillingPanel />);
  await waitFor(() => expect(screen.getByText("Básico")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: /excluir/i }));

  await waitFor(() => expect(screen.queryByText("Básico")).not.toBeInTheDocument());
  confirmSpy.mockRestore();
});
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd apps/web && pnpm vitest run EndCustomerBillingPanel`
Expected: FAIL — não existe `nome do pacote`/lista de pacotes no componente ainda.

- [ ] **Step 3: Implementar o CRUD de pacotes**

Adicionar tipos e estado em `apps/web/src/components/EndCustomerBillingPanel.tsx` (junto dos já existentes):

```typescript
type Package = {
  id: string;
  name: string;
  price_brl: string;
  credits_granted: number;
  active: boolean;
};

const EMPTY_PACKAGE_FORM = { name: "", price_brl: "", credits_granted: "" };
```

No corpo do componente, adicionar estado e carregamento dos pacotes:

```typescript
  const [packages, setPackages] = useState<Package[]>([]);
  const [packageForm, setPackageForm] = useState(EMPTY_PACKAGE_FORM);
  const [creatingPackage, setCreatingPackage] = useState(false);
```

Estender `load()` pra também buscar os pacotes:

```typescript
  async function load() {
    try {
      const [settingsResponse, packagesResponse] = await Promise.all([
        backendFetch("end-customer-billing/settings"),
        backendFetch("end-customer-billing/packages"),
      ]);
      if (settingsResponse.ok) {
        const body: Settings = await settingsResponse.json();
        setSettings(body);
        setEnabled(body.enabled);
        setTokensPerCredit(body.end_customer_tokens_per_credit?.toString() ?? "");
      }
      if (packagesResponse.ok) {
        setPackages(await packagesResponse.json());
      }
    } finally {
      setLoaded(true);
    }
  }
```

Adicionar os handlers de criar/excluir:

```typescript
  async function handleCreatePackage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setCreatingPackage(true);
    try {
      const response = await backendFetch("end-customer-billing/packages", {
        method: "POST",
        body: JSON.stringify({
          name: packageForm.name,
          price_brl: packageForm.price_brl,
          credits_granted: Number(packageForm.credits_granted),
        }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(body, "Falha ao criar pacote — tente novamente."));
        return;
      }
      setPackages([...packages, body]);
      setPackageForm(EMPTY_PACKAGE_FORM);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setCreatingPackage(false);
    }
  }

  async function handleDeletePackage(pkg: Package) {
    if (!window.confirm(`Excluir o pacote "${pkg.name}"?`)) return;
    try {
      const response = await backendFetch(`end-customer-billing/packages/${pkg.id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(extractErrorDetail(body, "Falha ao excluir — tente novamente."));
        return;
      }
      setPackages(packages.filter((p) => p.id !== pkg.id));
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    }
  }
```

Adicionar a UI dos pacotes depois do `</form>` das settings, ainda dentro da `<div className="flex-1 overflow-y-auto ...">`:

```typescript
        <hr className="my-6 border-line" />

        <h2 className="font-display text-lg font-semibold text-ink">Pacotes de crédito</h2>
        <ul className="mt-4 max-w-md">
          {packages.length === 0 && (
            <li className="py-4 text-sm text-muted">Nenhum pacote cadastrado ainda.</li>
          )}
          {packages.map((pkg) => (
            <li key={pkg.id} className="flex items-center justify-between border-b border-line py-3">
              <div>
                <p className="font-medium text-ink">{pkg.name}</p>
                <p className="text-xs text-muted">
                  R$ {pkg.price_brl} · {pkg.credits_granted} créditos
                </p>
              </div>
              <button
                type="button"
                onClick={() => void handleDeletePackage(pkg)}
                className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
              >
                Excluir
              </button>
            </li>
          ))}
        </ul>

        <form onSubmit={handleCreatePackage} className="mt-4 flex max-w-md flex-col gap-4">
          <label className="flex flex-col gap-1 text-sm text-ink">
            Nome do pacote
            <input
              required
              value={packageForm.name}
              onChange={(event) => setPackageForm({ ...packageForm, name: event.target.value })}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-ink">
            Preço (R$)
            <input
              required
              value={packageForm.price_brl}
              onChange={(event) => setPackageForm({ ...packageForm, price_brl: event.target.value })}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-ink">
            Créditos
            <input
              required
              type="number"
              min={1}
              value={packageForm.credits_granted}
              onChange={(event) => setPackageForm({ ...packageForm, credits_granted: event.target.value })}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <button
            type="submit"
            disabled={creatingPackage}
            className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
          >
            {creatingPackage ? "Adicionando..." : "Adicionar pacote"}
          </button>
        </form>
```

- [ ] **Step 4: Rodar o teste de novo**

Run: `pnpm vitest run EndCustomerBillingPanel`
Expected: PASS (todos os testes das Tasks 9 e 10).

- [ ] **Step 5: Rodar a suíte completa + lint do `web`**

Run: `pnpm test && pnpm lint`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/EndCustomerBillingPanel.tsx apps/web/__tests__/EndCustomerBillingPanel.test.tsx
git commit -m "feat(web): CRUD de pacotes de crédito do cliente final"
```

---

### Task 11: `web` — item de navegação

**Files:**
- Modify: `apps/web/src/components/TenantNav.tsx`
- Modify: `apps/web/__tests__/TenantNav.test.tsx`

**Interfaces:**
- Produces: `TenantNavItem` ganha o literal `"cobranca"` — resolve o `active="cobranca"` já usado na Task 9.

- [ ] **Step 1: Escrever o teste**

Adicionar à primeira asserção do teste `"renderiza o item ativo como texto (não link) e os demais como links"` em `apps/web/__tests__/TenantNav.test.tsx`, e um teste novo dedicado:

```typescript
  it("marca cobranca como ativo quando active='cobranca'", () => {
    render(<TenantNav active="cobranca" />);

    expect(screen.getByText("Cobrança").closest("a")).toBeNull();
    expect(screen.getByText("Conversas").closest("a")).toHaveAttribute("href", "/conversas");
  });
```

E, no teste já existente `"renderiza o item ativo como texto (não link) e os demais como links"`, adicionar a linha:

```typescript
    expect(screen.getByText("Cobrança").closest("a")).toHaveAttribute(
      "href",
      "/configuracoes/cobranca-clientes",
    );
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd apps/web && pnpm vitest run TenantNav`
Expected: FAIL — `screen.getByText("Cobrança")` não encontra o elemento.

- [ ] **Step 3: Implementar**

Em `apps/web/src/components/TenantNav.tsx`, trocar:

```typescript
type TenantNavItem = "inicio" | "conversas" | "base" | "config" | "creditos" | "perfil";

const ITEMS: { key: TenantNavItem; href: string; label: string }[] = [
  { key: "inicio", href: "/inicio", label: "Início" },
  { key: "conversas", href: "/conversas", label: "Conversas" },
  { key: "base", href: "/base-de-conhecimento", label: "Base" },
  { key: "config", href: "/configuracoes/whatsapp", label: "Config" },
  { key: "creditos", href: "/creditos", label: "Créditos" },
  { key: "perfil", href: "/perfil", label: "Perfil" },
];
```

por:

```typescript
type TenantNavItem = "inicio" | "conversas" | "base" | "config" | "cobranca" | "creditos" | "perfil";

const ITEMS: { key: TenantNavItem; href: string; label: string }[] = [
  { key: "inicio", href: "/inicio", label: "Início" },
  { key: "conversas", href: "/conversas", label: "Conversas" },
  { key: "base", href: "/base-de-conhecimento", label: "Base" },
  { key: "config", href: "/configuracoes/whatsapp", label: "Config" },
  { key: "cobranca", href: "/configuracoes/cobranca-clientes", label: "Cobrança" },
  { key: "creditos", href: "/creditos", label: "Créditos" },
  { key: "perfil", href: "/perfil", label: "Perfil" },
];
```

- [ ] **Step 4: Rodar o teste de novo**

Run: `pnpm vitest run TenantNav`
Expected: PASS (todos os testes, incluindo o novo).

- [ ] **Step 5: Rodar a suíte completa + lint + build do `web`**

Run: `pnpm test && pnpm lint && pnpm build`
Expected: PASS (o `pnpm build` confirma que o `active="cobranca"` da Task 9 agora tipa corretamente).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/TenantNav.tsx apps/web/__tests__/TenantNav.test.tsx
git commit -m "feat(web): item de navegação da cobrança do cliente final"
```

---

## Self-Review

**Cobertura do spec**: contrato `POST /messages` propagando saldo/pacotes ✅ (Task 5), tool + gate técnico no grafo ✅ (Tasks 6-7), secretária oferecendo pacotes ✅ (Task 8), débito do cliente final na mesma transação ✅ (Task 4), painel de configuração + CRUD de pacotes ✅ (Tasks 9-10), nav ✅ (Task 11).

**Dependência do Plano 1**: as Tasks 6, 9 e 10 assumem que `POST /api/v1/internal/end-customer-billing/checkout` e `GET/PATCH /api/v1/end-customer-billing/{settings,packages}` já existem e respondem exatamente como especificado nele — rode o Plano 1 primeiro.

**Fora deste plano**: nenhuma mudança em `apps/api_rag` (não há custo de créditos em ingestão/retrieval nesta entrega, igual já registrado como pendência no `CLAUDE.md`); nenhuma UI de histórico de compras/consumo do cliente final (fora de escopo do spec).
