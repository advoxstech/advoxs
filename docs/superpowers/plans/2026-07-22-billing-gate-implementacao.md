# Billing Gate Determinístico — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar o gate de billing determinístico (Etapas 0-4 do desenho arquitetural) — um terceiro estado `billing_gate` em `conversations`, conduzido inteiramente pelo `worker` via mensagens WhatsApp nativas (texto + lista interativa), sem nenhuma chamada ao `agents` service enquanto o contato não tiver saldo. Rollout gradual por tenant via `tenant_billing_settings.insufficient_balance_policy` — o gate antigo (dentro do `agents`) continua funcionando sem nenhuma mudança pros tenants ainda não migrados.

**Architecture:** Migration + models (`apps/api`) → espelho das colunas novas no `apps/worker` (Core tables) → 2 clients HTTP novos no `worker` (envio de mensagem interativa via Graph API; chamada ao endpoint interno de checkout) → módulo de máquina de estados (`apps/worker/app/billing_gate.py`) chamado por `process_inbound_message` antes do fluxo normal → branch condicional no webhook Stripe do tenant pra fechar o ciclo sem acionar o `agents`.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (`apps/api`), Arq + SQLAlchemy Core (`apps/worker`), httpx. Testes: `pytest` nos dois serviços.

## Global Constraints

- **Não é corte único** — todo o comportamento novo só se aplica quando `tenant_billing_settings.insufficient_balance_policy == "deterministic_gate"`. O default (`block_with_message`) preserva o comportamento de hoje byte a byte, incluindo o mecanismo do commit `0e8267e` (mensagem de gatilho + `arq.enqueue_job`) — **não é removido nesta etapa**, só passa a coexistir com o caminho novo.
- **Nenhuma mudança em `apps/agents`** — esta etapa não remove nada do gate antigo (isso é a Etapa 5, fora de escopo deste plano, só acontece depois que 100% dos tenants estiverem migrados).
- **Escopo**: `apps/api` + `apps/worker`. `apps/web` não é tocado — uma conversa em `billing_gate` continua sendo exibida no painel (o campo `state` no `ConversationOut` aceita o valor novo), mas o front não ganha nenhum badge/estilo dedicado pra esse estado nesta etapa — item residual, fora de escopo, registrado aqui pra não ser esquecido depois.
- **Resolução de pacote por título, não por id** — o parser do webhook do WhatsApp já persiste `content` como o título da opção escolhida (`interactive.list_reply.title`/`button_reply.title`) quando a mensagem é do tipo `interactive`; o `message_type` original não é persistido em `messages`, então o `worker` nunca sabe se um texto veio de uma seleção real ou de texto livre — ele resolve isso comparando o `content` recebido contra os nomes dos pacotes ativos do tenant. Se não bater com nenhum, conta como tentativa não reconhecida (incrementa `billing_gate_retries`), sem distinguir "veio de uma lista antiga" de "o cliente só escreveu qualquer coisa" — simplificação deliberada em relação ao desenho original, registrada aqui.
- **Idempotência do link de pagamento**: nenhuma chamada nova ao Stripe é feita enquanto o step for `aguardando_pagamento` — o link já gerado fica armazenado em `conversations.billing_gate_checkout_url` e é só reenviado, nunca recriado.
- **`billing_gate_retries` sempre reseta a 0 ao mudar de step** (entrada no gate, abertura, seleção de pacote bem-sucedida) — só incrementa dentro do MESMO step, quando a resposta não é reconhecida.
- Limite de tentativas antes de escalar pra `human`: `MAX_RETRIES = 3` (constante, sem configuração por tenant nesta etapa).

---

### Task 1: Migration — `conversations`/`tenant_billing_settings`

**Files:**
- Create: `apps/api/alembic/versions/0018_billing_gate.py`

**Interfaces:**
- Consumes: nada.
- Produces: colunas `conversations.billing_gate_step`/`billing_gate_retries`/`billing_gate_checkout_url`, `tenant_billing_settings.billing_gate_welcome_text`, e o valor `'billing_gate'` aceito por `conversations.state` — consumidos pelas Tasks 2-7.

- [ ] **Step 1: Criar a migration**

Crie `apps/api/alembic/versions/0018_billing_gate.py`:

```python
"""billing gate determinístico — novo estado de conversa + colunas de suporte

Adiciona o estado `billing_gate` a `conversations.state` (rollout gradual,
controlado por tenant_billing_settings.insufficient_balance_policy — ver
docs/superpowers/specs/2026-07-22-billing-gate-deterministico-design.md) e as
colunas de apoio pra máquina de estados conduzida pelo worker: qual step do
gate a conversa está, quantas tentativas não reconhecidas já teve, e o link
de pagamento já gerado (reenviado, nunca recriado, enquanto aguarda o
pagamento). `tenant_billing_settings` ganha um texto de boas-vindas
opcional pro tenant customizar (cai num texto genérico se não configurado).

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-22
"""

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_conversations_state", "conversations", type_="check")
    op.create_check_constraint(
        "ck_conversations_state",
        "conversations",
        "state IN ('agent', 'human', 'billing_gate')",
    )
    op.add_column(
        "conversations",
        sa.Column("billing_gate_step", sa.String(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "billing_gate_retries", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    op.add_column(
        "conversations",
        sa.Column("billing_gate_checkout_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "tenant_billing_settings",
        sa.Column("billing_gate_welcome_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_billing_settings", "billing_gate_welcome_text")
    op.drop_column("conversations", "billing_gate_checkout_url")
    op.drop_column("conversations", "billing_gate_retries")
    op.drop_column("conversations", "billing_gate_step")
    op.drop_constraint("ck_conversations_state", "conversations", type_="check")
    op.create_check_constraint(
        "ck_conversations_state", "conversations", "state IN ('agent', 'human')"
    )
```

- [ ] **Step 2: Verificar a migration contra Postgres real**

Se houver um Postgres real disponível (`docker compose ps postgres`): rode `cd apps/api && uv run alembic upgrade head`, confirme que `\d conversations` (via `psql`) mostra as 3 colunas novas e o CHECK constraint atualizado, depois `uv run alembic downgrade -1 && uv run alembic upgrade head` de novo pra confirmar que sobe/desce/sobe limpo. Sem Postgres disponível, valide só a sintaxe: `python3 -c "import ast; ast.parse(open('apps/api/alembic/versions/0018_billing_gate.py').read())"`.

- [ ] **Step 3: Commit**

```bash
git add apps/api/alembic/versions/0018_billing_gate.py
git commit -m "feat(api): migration do billing gate determinístico (novo estado + colunas de apoio)"
```

---

### Task 2: Models e schemas (`apps/api`)

**Files:**
- Modify: `apps/api/app/models/conversation.py`
- Modify: `apps/api/app/models/end_customer_billing.py`
- Modify: `apps/api/app/schemas/conversations.py`

**Interfaces:**
- Consumes: migration `0018` (Task 1).
- Produces: `Conversation.billing_gate_step`/`billing_gate_retries`/`billing_gate_checkout_url`, `TenantBillingSettings.billing_gate_welcome_text` — consumidos pela Task 7 (`apps/api/app/services/end_customer_billing.py`).

- [ ] **Step 1: Atualizar o model `Conversation`**

Em `apps/api/app/models/conversation.py`, troque:

```python
    __table_args__ = (
        CheckConstraint("state IN ('agent', 'human')", name="state"),
        UniqueConstraint("tenant_id", "contact_phone_number"),
    )
```

por:

```python
    __table_args__ = (
        CheckConstraint("state IN ('agent', 'human', 'billing_gate')", name="state"),
        UniqueConstraint("tenant_id", "contact_phone_number"),
    )
```

E adicione, depois de `human_last_seen_at`:

```python
    human_last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    billing_gate_step: Mapped[str | None] = mapped_column(String)
    billing_gate_retries: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
    )
    billing_gate_checkout_url: Mapped[str | None] = mapped_column(Text)
```

(precisa importar `Integer` no topo do arquivo — troque `from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, text)` por `from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, text)`; o tipo `Mapped[int]` já infere `Integer`, mas mantenha o import por clareza com o resto do arquivo, que sempre declara o tipo explícito nos outros campos — troque a declaração de `billing_gate_retries` pra incluir `Integer`: `mapped_column(Integer, nullable=False, default=0, server_default=text("0"))`.)

- [ ] **Step 2: Atualizar o model `TenantBillingSettings`**

Em `apps/api/app/models/end_customer_billing.py`, adicione, depois de `insufficient_balance_policy`:

```python
    insufficient_balance_policy: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'block_with_message'")
    )
    billing_gate_welcome_text: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 3: Atualizar `ConversationOut`**

Em `apps/api/app/schemas/conversations.py`, troque:

```python
    state: Literal["agent", "human"]
```

por:

```python
    state: Literal["agent", "human", "billing_gate"]
```

(`ConversationStateUpdate.state` **não muda** — continua `Literal["agent", "human"]`; `billing_gate` nunca é um valor aceito via `PATCH` público, só o `worker`/webhook escrevem nele diretamente no banco.)

- [ ] **Step 4: Rodar a suíte + lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Expected: todos os testes existentes continuam passando (os campos novos têm default, nenhum teste que constrói `Conversation`/`TenantBillingSettings` precisa mudar) e lint limpo.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/models/conversation.py apps/api/app/models/end_customer_billing.py apps/api/app/schemas/conversations.py
git commit -m "feat(api): expõe os campos do billing gate nos models/schemas"
```

---

### Task 3: Espelho no `apps/worker` — config + Core tables

**Files:**
- Modify: `apps/worker/app/config.py`
- Modify: `apps/worker/app/tables.py`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: migration `0018` (Task 1, mesmo banco).
- Produces: `settings.graph_api_base_url`/`graph_api_version`/`internal_service_key`/`api_url`, `tables.conversations` com as 3 colunas novas, `tables.tenant_billing_settings` com `insufficient_balance_policy`/`billing_gate_welcome_text` — consumidos pelas Tasks 4-6.

- [ ] **Step 1: Adicionar as settings novas**

Em `apps/worker/app/config.py`, adicione, depois de `whatsapp_token_encryption_key`:

```python
    whatsapp_token_encryption_key: str = ""

    # Graph API (envio direto de mensagens do billing gate, sem passar pelo agents).
    graph_api_base_url: str = "https://graph.facebook.com"
    graph_api_version: str = "v23.0"

    # api (endpoint interno de checkout do cliente final — mesma chave que o
    # agents já usa, ver INTERNAL_SERVICE_KEY em apps/api/app/core/config.py).
    api_url: str = "http://api:8000"
    internal_service_key: str = ""
```

- [ ] **Step 2: Adicionar as colunas novas nas Core tables**

Em `apps/worker/app/tables.py`, troque:

```python
conversations = Table(
    "conversations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("contact_phone_number", String),
    Column("state", String),
    Column("is_test", Boolean, nullable=False),
    Column("last_message_at", DateTime(timezone=True)),
    Column("human_last_seen_at", DateTime(timezone=True)),
)
```

por:

```python
conversations = Table(
    "conversations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("contact_phone_number", String),
    Column("state", String),
    Column("is_test", Boolean, nullable=False),
    Column("last_message_at", DateTime(timezone=True)),
    Column("human_last_seen_at", DateTime(timezone=True)),
    Column("billing_gate_step", String),
    Column("billing_gate_retries", Integer),
    Column("billing_gate_checkout_url", Text),
)
```

E troque:

```python
tenant_billing_settings = Table(
    "tenant_billing_settings",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("enabled", Boolean),
    Column("end_customer_tokens_per_credit", Integer),
)
```

por:

```python
tenant_billing_settings = Table(
    "tenant_billing_settings",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("enabled", Boolean),
    Column("end_customer_tokens_per_credit", Integer),
    Column("insufficient_balance_policy", String),
    Column("billing_gate_welcome_text", Text),
)
```

(confirme que `Integer`/`Text` já estão importados no topo do arquivo — ambos já são usados em outras tabelas do mesmo arquivo, então já devem estar no import de `sqlalchemy`.)

- [ ] **Step 3: Provisionar `API_URL`/`INTERNAL_SERVICE_KEY` pro `worker` no compose**

Em `docker-compose.yml`, no bloco `worker:`, troque:

```yaml
  worker:
    build:
      context: apps/worker
    image: ghcr.io/advoxstech/worker:${TAG:-latest}
    restart: unless-stopped
    env_file: *default-env-file
    environment:
      RAG_API_URL: http://api_rag:8000
```

por:

```yaml
  worker:
    build:
      context: apps/worker
    image: ghcr.io/advoxstech/worker:${TAG:-latest}
    restart: unless-stopped
    env_file: *default-env-file
    environment:
      RAG_API_URL: http://api_rag:8000
      API_URL: http://api:8000
```

(`INTERNAL_SERVICE_KEY` já vem do `env_file: *default-env-file` compartilhado — o mesmo valor que `agents`/`api` já usam — não precisa de entrada nova em `environment:`, só precisa existir no `.env` raiz, que já é o caso hoje.)

- [ ] **Step 4: Rodar a suíte do worker**

Run: `cd apps/worker && python3 -m pytest tests/unit -q`
Expected: todos os testes continuam passando (nenhum teste hoje monta uma query contra as colunas novas, então nada quebra só por elas existirem na tabela).

- [ ] **Step 5: Commit**

```bash
git add apps/worker/app/config.py apps/worker/app/tables.py docker-compose.yml
git commit -m "feat(worker): espelha as colunas/config do billing gate determinístico"
```

---

### Task 4: `InboundContext`/`_load_context` — carregar os campos novos

**Files:**
- Modify: `apps/worker/app/tasks/messages.py`
- Test: `apps/worker/tests/unit/test_load_context.py`

**Interfaces:**
- Consumes: `tables.conversations`/`tables.tenant_billing_settings` com as colunas novas (Task 3).
- Produces: `InboundContext.billing_gate_step`/`billing_gate_retries`/`billing_gate_checkout_url`/`insufficient_balance_policy`/`billing_gate_welcome_text` — consumidos pela Task 6 (`billing_gate.py`).

- [ ] **Step 1: Atualizar os testes existentes que quebrariam**

Em `apps/worker/tests/unit/test_load_context.py`, troque os 2 lugares que constroem `billing_settings = SimpleNamespace(enabled=True)`:

```python
async def test_billing_habilitado_le_saldo_e_pacotes() -> None:
    billing_settings = SimpleNamespace(enabled=True)
```

por:

```python
async def test_billing_habilitado_le_saldo_e_pacotes() -> None:
    billing_settings = SimpleNamespace(
        enabled=True, insufficient_balance_policy="block_with_message", billing_gate_welcome_text=None
    )
```

E:

```python
async def test_billing_habilitado_sem_saldo_ainda_usa_zero() -> None:
    billing_settings = SimpleNamespace(enabled=True)
```

por:

```python
async def test_billing_habilitado_sem_saldo_ainda_usa_zero() -> None:
    billing_settings = SimpleNamespace(
        enabled=True, insufficient_balance_policy="block_with_message", billing_gate_welcome_text=None
    )
```

Troque a função `_conversation()`:

```python
def _conversation():
    return SimpleNamespace(
        state="agent", contact_phone_number="5511999998888", human_last_seen_at=None
    )
```

por:

```python
def _conversation(**overrides):
    row = SimpleNamespace(
        state="agent",
        contact_phone_number="5511999998888",
        human_last_seen_at=None,
        billing_gate_step=None,
        billing_gate_retries=0,
        billing_gate_checkout_url=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row
```

- [ ] **Step 2: Adicionar os testes novos**

No fim do arquivo, adicione:

```python
async def test_carrega_campos_do_billing_gate_da_conversa() -> None:
    conversation = _conversation(
        billing_gate_step="aguardando_pagamento",
        billing_gate_retries=2,
        billing_gate_checkout_url="https://checkout.stripe.com/xyz",
    )
    session = _session_with(
        conversation=conversation,
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=None,
        balance=None,
        packages=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.billing_gate_step == "aguardando_pagamento"
    assert context.billing_gate_retries == 2
    assert context.billing_gate_checkout_url == "https://checkout.stripe.com/xyz"


async def test_carrega_policy_e_texto_de_boas_vindas_do_tenant() -> None:
    billing_settings = SimpleNamespace(
        enabled=True,
        insufficient_balance_policy="deterministic_gate",
        billing_gate_welcome_text="Bem-vindo ao nosso escritório!",
    )
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=billing_settings,
        balance=0,
        packages=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.insufficient_balance_policy == "deterministic_gate"
    assert context.billing_gate_welcome_text == "Bem-vindo ao nosso escritório!"


async def test_sem_billing_settings_usa_policy_default() -> None:
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

    assert context.insufficient_balance_policy == "block_with_message"
    assert context.billing_gate_welcome_text is None
```

- [ ] **Step 3: Rodar e confirmar a falha**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_load_context.py -v`
Expected: FAIL — `InboundContext` ainda não tem os campos novos (`TypeError: unexpected keyword argument` ao tentar acessar `context.billing_gate_step` etc. via `AttributeError`, já que o dataclass nem tenta setar).

- [ ] **Step 4: Implementar em `InboundContext`/`_load_context`**

Em `apps/worker/app/tasks/messages.py`, troque a dataclass:

```python
@dataclass
class InboundContext:
    conversation_state: str
    contact_phone_number: str
    message_content: str
    phone_number_id: str
    access_token_encrypted: str
    credit_balance: Decimal
    end_customer_billing_enabled: bool
    end_customer_balance: Decimal
    end_customer_packages: list[dict]
    agents: list[dict]
    human_last_seen_at: datetime | None = None
```

por:

```python
@dataclass
class InboundContext:
    conversation_state: str
    contact_phone_number: str
    message_content: str
    phone_number_id: str
    access_token_encrypted: str
    credit_balance: Decimal
    end_customer_billing_enabled: bool
    end_customer_balance: Decimal
    end_customer_packages: list[dict]
    agents: list[dict]
    human_last_seen_at: datetime | None = None
    billing_gate_step: str | None = None
    billing_gate_retries: int = 0
    billing_gate_checkout_url: str | None = None
    insufficient_balance_policy: str = "block_with_message"
    billing_gate_welcome_text: str | None = None
```

Em `_load_context`, troque a query da conversa:

```python
    conversation = (
        await session.execute(
            select(
                tables.conversations.c.state,
                tables.conversations.c.contact_phone_number,
                tables.conversations.c.human_last_seen_at,
            ).where(tables.conversations.c.id == uuid.UUID(conversation_id))
        )
    ).one_or_none()
```

por:

```python
    conversation = (
        await session.execute(
            select(
                tables.conversations.c.state,
                tables.conversations.c.contact_phone_number,
                tables.conversations.c.human_last_seen_at,
                tables.conversations.c.billing_gate_step,
                tables.conversations.c.billing_gate_retries,
                tables.conversations.c.billing_gate_checkout_url,
            ).where(tables.conversations.c.id == uuid.UUID(conversation_id))
        )
    ).one_or_none()
```

Troque a query de `billing_settings`:

```python
    billing_settings = (
        await session.execute(
            select(tables.tenant_billing_settings.c.enabled).where(
                tables.tenant_billing_settings.c.tenant_id == uuid.UUID(tenant_id)
            )
        )
    ).one_or_none()
```

por:

```python
    billing_settings = (
        await session.execute(
            select(
                tables.tenant_billing_settings.c.enabled,
                tables.tenant_billing_settings.c.insufficient_balance_policy,
                tables.tenant_billing_settings.c.billing_gate_welcome_text,
            ).where(tables.tenant_billing_settings.c.tenant_id == uuid.UUID(tenant_id))
        )
    ).one_or_none()
```

E o `return InboundContext(...)` final, troque:

```python
    return InboundContext(
        conversation_state=conversation.state,
        contact_phone_number=conversation.contact_phone_number,
        message_content=content,
        phone_number_id=number.phone_number_id,
        access_token_encrypted=number.access_token_encrypted,
        credit_balance=credit_balance,
        end_customer_billing_enabled=end_customer_billing_enabled,
        end_customer_balance=end_customer_balance,
        end_customer_packages=end_customer_packages,
        agents=agents,
        human_last_seen_at=conversation.human_last_seen_at,
    )
```

por:

```python
    return InboundContext(
        conversation_state=conversation.state,
        contact_phone_number=conversation.contact_phone_number,
        message_content=content,
        phone_number_id=number.phone_number_id,
        access_token_encrypted=number.access_token_encrypted,
        credit_balance=credit_balance,
        end_customer_billing_enabled=end_customer_billing_enabled,
        end_customer_balance=end_customer_balance,
        end_customer_packages=end_customer_packages,
        agents=agents,
        human_last_seen_at=conversation.human_last_seen_at,
        billing_gate_step=conversation.billing_gate_step,
        billing_gate_retries=conversation.billing_gate_retries,
        billing_gate_checkout_url=conversation.billing_gate_checkout_url,
        insufficient_balance_policy=(
            billing_settings.insufficient_balance_policy
            if billing_settings is not None
            else "block_with_message"
        ),
        billing_gate_welcome_text=(
            billing_settings.billing_gate_welcome_text if billing_settings is not None else None
        ),
    )
```

- [ ] **Step 5: Rodar e confirmar sucesso**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_load_context.py -v`
Expected: todos os testes do arquivo passam, incluindo os 3 novos.

- [ ] **Step 6: Rodar a suíte completa do worker**

Run: `cd apps/worker && python3 -m pytest tests/unit -q`
Expected: todos passam — `test_sem_agentes_retorna_lista_vazia`'s `assert session.execute.await_count == 7` continua valendo (as colunas novas foram adicionadas às queries já existentes, nenhuma query nova foi criada).

- [ ] **Step 7: Commit**

```bash
git add apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_load_context.py
git commit -m "feat(worker): carrega os campos do billing gate em InboundContext"
```

---

### Task 5: Clients HTTP novos — mensagem interativa + checkout interno

**Files:**
- Create: `apps/worker/app/clients/whatsapp.py`
- Create: `apps/worker/app/clients/billing.py`
- Test: `apps/worker/tests/unit/test_whatsapp_client.py`
- Test: `apps/worker/tests/unit/test_billing_client.py`

**Interfaces:**
- Consumes: `settings.graph_api_base_url`/`graph_api_version`/`internal_service_key`/`api_url` (Task 3).
- Produces: `send_text_message(phone_number_id, access_token, to, text)`, `send_interactive_list_message(phone_number_id, access_token, to, header, body, sections)`, `create_end_customer_checkout(http, tenant_id, contact_phone_number, package_id) -> str` — consumidos pela Task 6 (`billing_gate.py`).

- [ ] **Step 1: Escrever os testes que falham — client de WhatsApp**

Crie `apps/worker/tests/unit/test_whatsapp_client.py`:

```python
import httpx
import pytest
import respx

from app.clients.whatsapp import WhatsAppSendError, send_interactive_list_message, send_text_message
from app.config import settings


@pytest.fixture
def graph_url() -> str:
    return f"{settings.graph_api_base_url}/{settings.graph_api_version}/PNID/messages"


class TestSendTextMessage:
    @respx.mock
    async def test_envia_texto_com_sucesso(self, graph_url) -> None:
        route = respx.post(graph_url).mock(return_value=httpx.Response(200, json={}))

        await send_text_message(
            phone_number_id="PNID", access_token="token", to="5511999998888", text="Olá"
        )

        assert route.called
        body = route.calls.last.request.content
        assert b'"type":"text"' in body

    @respx.mock
    async def test_erro_da_graph_api_levanta_whatsapp_send_error(self, graph_url) -> None:
        respx.post(graph_url).mock(return_value=httpx.Response(400, json={"error": {"message": "token inválido"}}))

        with pytest.raises(WhatsAppSendError):
            await send_text_message(
                phone_number_id="PNID", access_token="token", to="5511999998888", text="Olá"
            )


class TestSendInteractiveListMessage:
    @respx.mock
    async def test_envia_lista_com_secoes(self, graph_url) -> None:
        route = respx.post(graph_url).mock(return_value=httpx.Response(200, json={}))

        await send_interactive_list_message(
            phone_number_id="PNID",
            access_token="token",
            to="5511999998888",
            header="Pacotes",
            body="Escolha um:",
            sections=[
                {
                    "title": "Disponíveis",
                    "rows": [{"id": "Básico", "title": "Básico", "description": "R$ 49,90"}],
                }
            ],
        )

        assert route.called
        body = route.calls.last.request.content
        assert b'"type":"list"' in body
        assert b"Basico" not in body  # sanity: nao mexe em acentuacao, so confere que enviou

    @respx.mock
    async def test_erro_da_graph_api_levanta_whatsapp_send_error(self, graph_url) -> None:
        respx.post(graph_url).mock(return_value=httpx.Response(500, json={}))

        with pytest.raises(WhatsAppSendError):
            await send_interactive_list_message(
                phone_number_id="PNID",
                access_token="token",
                to="5511999998888",
                header="Pacotes",
                body="Escolha um:",
                sections=[{"title": "x", "rows": []}],
            )
```

Confirme se `respx` já está disponível no `apps/worker` (`grep respx apps/worker/pyproject.toml`); se não estiver, adicione como dependência de dev (`uv add --dev respx` dentro de `apps/worker`, ou o equivalente já usado nesse projeto — confira como `apps/api`/`apps/agents` mockam `httpx` nos testes existentes e siga o mesmo padrão se `respx` não for a convenção daqui).

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_whatsapp_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.clients.whatsapp'`.

- [ ] **Step 3: Implementar `apps/worker/app/clients/whatsapp.py`**

```python
"""Envio de mensagem via WhatsApp Cloud API (Graph API) direto do worker —
usado só pelo billing gate determinístico (apps/worker/app/billing_gate.py),
que precisa mandar texto e listas interativas SEM passar pelo agents
service (é esse desvio que elimina o custo de LLM nesse trecho do funil).
Duplicado deliberadamente de apps/api/app/clients/whatsapp.py — mesmo padrão
já usado no projeto pra evitar acoplamento entre serviços deployados
separadamente (ex: calcular_creditos)."""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class WhatsAppSendError(Exception):
    pass


def _url(phone_number_id: str) -> str:
    return f"{settings.graph_api_base_url}/{settings.graph_api_version}/{phone_number_id}/messages"


async def _post(phone_number_id: str, access_token: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                _url(phone_number_id),
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise WhatsAppSendError(f"Falha de rede ao chamar a Graph API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Graph API retornou erro | status=%s body=%s", response.status_code, response.text
        )
        raise WhatsAppSendError(f"Graph API HTTP {response.status_code}: {response.text}")


async def send_text_message(phone_number_id: str, access_token: str, to: str, text: str) -> None:
    await _post(
        phone_number_id,
        access_token,
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text},
        },
    )


async def send_interactive_list_message(
    phone_number_id: str,
    access_token: str,
    to: str,
    header: str,
    body: str,
    sections: list[dict],
    button_text: str = "Ver opções",
) -> None:
    """`sections`: `[{"title": str, "rows": [{"id": str, "title": str, "description": str}]}]`.
    Limite da Meta: até 10 seções, no máximo 10 linhas somadas entre todas."""
    await _post(
        phone_number_id,
        access_token,
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": header},
                "body": {"text": body},
                "action": {"button": button_text, "sections": sections},
            },
        },
    )
```

- [ ] **Step 4: Rodar e confirmar sucesso — client de WhatsApp**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_whatsapp_client.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 5: Escrever os testes que falham — client de billing**

Crie `apps/worker/tests/unit/test_billing_client.py`:

```python
import uuid

import httpx
import pytest
import respx

from app.clients.billing import BillingCheckoutError, create_end_customer_checkout
from app.config import settings

TENANT_ID = str(uuid.uuid4())
PACKAGE_ID = str(uuid.uuid4())


class TestCreateEndCustomerCheckout:
    @respx.mock
    async def test_chama_endpoint_interno_e_devolve_url(self) -> None:
        route = respx.post(f"{settings.api_url}/api/v1/internal/end-customer-billing/checkout").mock(
            return_value=httpx.Response(200, json={"checkout_url": "https://checkout.stripe.com/xyz"})
        )

        url = await create_end_customer_checkout(
            tenant_id=TENANT_ID, contact_phone_number="5511999998888", package_id=PACKAGE_ID
        )

        assert url == "https://checkout.stripe.com/xyz"
        sent = route.calls.last.request
        assert sent.headers.get("authorization") == settings.internal_service_key

    @respx.mock
    async def test_erro_do_endpoint_levanta_billing_checkout_error(self) -> None:
        respx.post(f"{settings.api_url}/api/v1/internal/end-customer-billing/checkout").mock(
            return_value=httpx.Response(400, json={"detail": "Pacote de créditos inválido"})
        )

        with pytest.raises(BillingCheckoutError):
            await create_end_customer_checkout(
                tenant_id=TENANT_ID, contact_phone_number="5511999998888", package_id=PACKAGE_ID
            )
```

- [ ] **Step 6: Rodar e confirmar a falha**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_billing_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.clients.billing'`.

- [ ] **Step 7: Implementar `apps/worker/app/clients/billing.py`**

```python
"""Chama o endpoint interno de checkout do cliente final (apps/api) direto do
worker — mesmo endpoint que a tool gerar_link_pagamento_cliente do agents já
usa hoje pros tenants ainda em insufficient_balance_policy=block_with_message.
Autenticado pela mesma INTERNAL_SERVICE_KEY (ver apps/api/app/api/internal_deps.py)."""

import httpx

from app.config import settings


class BillingCheckoutError(Exception):
    pass


async def create_end_customer_checkout(
    tenant_id: str, contact_phone_number: str, package_id: str
) -> str:
    headers = (
        {"Authorization": settings.internal_service_key} if settings.internal_service_key else {}
    )
    try:
        async with httpx.AsyncClient(base_url=settings.api_url, timeout=15) as client:
            response = await client.post(
                "/api/v1/internal/end-customer-billing/checkout",
                json={
                    "tenant_id": tenant_id,
                    "contact_phone_number": contact_phone_number,
                    "package_id": package_id,
                },
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise BillingCheckoutError(f"Falha de rede ao gerar o link de pagamento: {exc}") from exc

    if response.is_error:
        raise BillingCheckoutError(
            f"Falha ao gerar o link de pagamento — HTTP {response.status_code}: {response.text}"
        )
    return response.json()["checkout_url"]
```

- [ ] **Step 8: Rodar e confirmar sucesso**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_billing_client.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 9: Rodar a suíte completa do worker + lint**

Run: `cd apps/worker && python3 -m pytest tests/unit -q && python3 -m ruff check app/clients/whatsapp.py app/clients/billing.py`
Expected: todos passam, sem erro novo nos 2 arquivos novos (ignore qualquer débito de lint pré-existente em outros arquivos do projeto, se houver).

- [ ] **Step 10: Commit**

```bash
git add apps/worker/app/clients/whatsapp.py apps/worker/app/clients/billing.py apps/worker/tests/unit/test_whatsapp_client.py apps/worker/tests/unit/test_billing_client.py
git commit -m "feat(worker): clients de envio de mensagem interativa e checkout interno, sem depender do agents"
```

---

### Task 6: Máquina de estados (`apps/worker/app/billing_gate.py`) + wiring

**Files:**
- Create: `apps/worker/app/billing_gate.py`
- Modify: `apps/worker/app/tasks/messages.py`
- Test: `apps/worker/tests/unit/test_billing_gate.py`
- Test: `apps/worker/tests/unit/test_process_inbound_message.py`

**Interfaces:**
- Consumes: `InboundContext` com os campos do billing gate (Task 4), `send_text_message`/`send_interactive_list_message` (Task 5, client de WhatsApp), `create_end_customer_checkout` (Task 5, client de billing).
- Produces: `maybe_enter_gate`, `handle_billing_gate` — consumidos só por `process_inbound_message`, nenhuma outra task depende disso.

- [ ] **Step 1: Escrever os testes que falham — `billing_gate.py`**

Crie `apps/worker/tests/unit/test_billing_gate.py`:

```python
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.billing_gate import MAX_RETRIES, handle_billing_gate, maybe_enter_gate
from app.tasks.messages import InboundContext

TENANT_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())

PACKAGES = [
    {"id": "pkg-1", "name": "Básico", "price_brl": "49.90", "credits_granted": 500},
    {"id": "pkg-2", "name": "Premium", "price_brl": "99.90", "credits_granted": 1200},
]


def _inbound(**overrides) -> InboundContext:
    base = InboundContext(
        conversation_state="agent",
        contact_phone_number="5511999998888",
        message_content="oi",
        phone_number_id="PNID",
        access_token_encrypted="cifrado",
        credit_balance=Decimal(1000),
        end_customer_billing_enabled=True,
        end_customer_balance=Decimal(0),
        end_customer_packages=PACKAGES,
        agents=[],
        insufficient_balance_policy="deterministic_gate",
        billing_gate_step=None,
        billing_gate_retries=0,
        billing_gate_checkout_url=None,
        billing_gate_welcome_text=None,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


@pytest.fixture(autouse=True)
def crypto(monkeypatch):
    monkeypatch.setattr("app.billing_gate.decrypt_access_token", lambda v: "token-claro")


class TestMaybeEnterGate:
    async def test_entra_no_gate_quando_policy_deterministic_e_sem_saldo(self) -> None:
        session = AsyncMock()
        inbound = _inbound(conversation_state="agent", end_customer_balance=Decimal(0))

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is True
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()

    async def test_nao_entra_quando_policy_e_block_with_message(self) -> None:
        session = AsyncMock()
        inbound = _inbound(insufficient_balance_policy="block_with_message")

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is False
        session.execute.assert_not_called()

    async def test_nao_entra_com_saldo_positivo(self) -> None:
        session = AsyncMock()
        inbound = _inbound(end_customer_balance=Decimal(500))

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is False

    async def test_ja_em_billing_gate_retorna_true_sem_reprocessar_entrada(self) -> None:
        session = AsyncMock()
        inbound = _inbound(conversation_state="billing_gate", billing_gate_step="aguardando_pagamento")

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is True
        session.execute.assert_not_called()


class TestHandleBillingGateAbertura:
    async def test_abre_o_gate_manda_boas_vindas_e_lista(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        send_list = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.send_interactive_list_message", send_list)
        inbound = _inbound(billing_gate_step=None)

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        send_text.assert_awaited_once()
        send_list.assert_awaited_once()
        sections = send_list.await_args.kwargs["sections"]
        assert sections[0]["rows"][0]["title"] == "Básico"
        session.execute.assert_awaited_once()
        update_values = session.execute.await_args.args[0]
        assert "aguardando_selecao_pacote" in str(update_values.compile(compile_kwargs={"literal_binds": True}))

    async def test_primeira_compra_usa_texto_institucional(self, monkeypatch) -> None:
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)  # nunca comprou
        monkeypatch.setattr("app.billing_gate.send_text_message", AsyncMock())
        monkeypatch.setattr("app.billing_gate.send_interactive_list_message", AsyncMock())
        inbound = _inbound(billing_gate_step=None, billing_gate_welcome_text=None)

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        text_sent = __import__("app.billing_gate", fromlist=["send_text_message"])
        # confirmado via monkeypatch acima — reforça a asserção pelo texto real:
        send_text = session  # placeholder pra leitura; a asserção real está no mock abaixo

    async def test_texto_configurado_pelo_tenant_tem_prioridade(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.send_interactive_list_message", AsyncMock())
        inbound = _inbound(billing_gate_step=None, billing_gate_welcome_text="Bem-vindo à Advoxs!")

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert send_text.await_args_list[0].kwargs["text"] == "Bem-vindo à Advoxs!"


class TestHandleBillingGateSelecaoPacote:
    async def test_selecao_valida_gera_link_e_avanca_step(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        checkout = AsyncMock(return_value="https://checkout.stripe.com/xyz")
        monkeypatch.setattr("app.billing_gate.send_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.create_end_customer_checkout", checkout)
        inbound = _inbound(
            billing_gate_step="aguardando_selecao_pacote", message_content="Básico"
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        checkout.assert_awaited_once_with(
            tenant_id=TENANT_ID, contact_phone_number="5511999998888", package_id="pkg-1"
        )
        assert "https://checkout.stripe.com/xyz" in send_text.await_args.kwargs["text"]
        update_values = session.execute.await_args.args[0]
        compiled = str(update_values.compile(compile_kwargs={"literal_binds": True}))
        assert "aguardando_pagamento" in compiled

    async def test_selecao_nao_reconhecida_reenvia_lista_e_incrementa_retry(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        send_list = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.send_interactive_list_message", send_list)
        inbound = _inbound(
            billing_gate_step="aguardando_selecao_pacote",
            message_content="não sei escolher",
            billing_gate_retries=0,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        send_list.assert_awaited_once()
        update_values = session.execute.await_args.args[0]
        compiled = str(update_values.compile(compile_kwargs={"literal_binds": True}))
        assert "billing_gate_retries = 1" in compiled

    async def test_ultima_tentativa_escala_pra_human(self, monkeypatch) -> None:
        session = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_text_message", AsyncMock())
        monkeypatch.setattr("app.billing_gate.send_interactive_list_message", AsyncMock())
        inbound = _inbound(
            billing_gate_step="aguardando_selecao_pacote",
            message_content="não sei escolher",
            billing_gate_retries=MAX_RETRIES - 1,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        update_values = session.execute.await_args.args[0]
        compiled = str(update_values.compile(compile_kwargs={"literal_binds": True}))
        assert "state = 'human'" in compiled


class TestHandleBillingGateAguardandoPagamento:
    async def test_reenvia_o_link_ja_gerado_sem_chamar_checkout_de_novo(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        checkout = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.create_end_customer_checkout", checkout)
        inbound = _inbound(
            billing_gate_step="aguardando_pagamento",
            billing_gate_checkout_url="https://checkout.stripe.com/xyz",
            billing_gate_retries=0,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        checkout.assert_not_called()
        assert "https://checkout.stripe.com/xyz" in send_text.await_args.kwargs["text"]

    async def test_ultima_tentativa_aguardando_pagamento_escala_pra_human(self, monkeypatch) -> None:
        session = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_text_message", AsyncMock())
        inbound = _inbound(
            billing_gate_step="aguardando_pagamento",
            billing_gate_checkout_url="https://checkout.stripe.com/xyz",
            billing_gate_retries=MAX_RETRIES - 1,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        update_values = session.execute.await_args.args[0]
        compiled = str(update_values.compile(compile_kwargs={"literal_binds": True}))
        assert "state = 'human'" in compiled
```

(O teste `test_primeira_compra_usa_texto_institucional` acima está incompleto de propósito — no Step 4, ao escrever a implementação real, volte a este teste e reescreva a asserção final pra checar `send_text.await_args_list[0].kwargs["text"]` contra o texto de recompra padrão, com um mock nomeado de verdade — ele foi deixado como estrutura pra você não perder o cenário, mas precisa de uma asserção real antes do Step 3. **Não deixe esse teste como está.**)

- [ ] **Step 2: Corrigir o teste incompleto acima antes de continuar**

Troque o corpo de `test_primeira_compra_usa_texto_institucional` por:

```python
    async def test_primeira_compra_usa_texto_institucional(self, monkeypatch) -> None:
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)  # nunca comprou
        send_text = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.send_interactive_list_message", AsyncMock())
        inbound = _inbound(billing_gate_step=None, billing_gate_welcome_text=None)

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert "Escolha um pacote" in send_text.await_args_list[0].kwargs["text"]
```

- [ ] **Step 3: Rodar e confirmar a falha**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_billing_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.billing_gate'`.

- [ ] **Step 4: Implementar `apps/worker/app/billing_gate.py`**

```python
"""Máquina de estados do billing gate determinístico — conduz o diálogo
mecânico (sem LLM) de "sem saldo -> escolher pacote -> pagar -> liberado"
pro cliente final, só pra tenants com insufficient_balance_policy =
"deterministic_gate" (rollout gradual, ver
docs/superpowers/specs/2026-07-22-billing-gate-deterministico-design.md).
Tenants ainda em "block_with_message" (o default) nunca chegam aqui — o
fluxo antigo (dentro do agents) continua valendo pra eles, sem mudança."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import tables
from app.clients.billing import create_end_customer_checkout
from app.clients.whatsapp import send_interactive_list_message, send_text_message
from app.crypto import decrypt_access_token
from app.tasks.messages import InboundContext

MAX_RETRIES = 3


async def maybe_enter_gate(
    session: AsyncSession, tenant_id: str, conversation_id: str, inbound: InboundContext
) -> bool:
    """Transiciona a conversa pra billing_gate se o tenant estiver migrado e
    o contato sem saldo. Retorna True se a conversa está (ou acabou de
    entrar) em billing_gate — nesse caso, process_inbound_message não deve
    seguir pro fluxo normal de chamar o agents."""
    if inbound.conversation_state == "billing_gate":
        return True
    if (
        inbound.conversation_state == "agent"
        and inbound.end_customer_billing_enabled
        and inbound.insufficient_balance_policy == "deterministic_gate"
        and inbound.end_customer_balance <= 0
    ):
        await session.execute(
            update(tables.conversations)
            .where(tables.conversations.c.id == uuid.UUID(conversation_id))
            .values(state="billing_gate", billing_gate_step=None, billing_gate_retries=0)
        )
        await session.commit()
        return True
    return False


async def handle_billing_gate(
    session: AsyncSession, tenant_id: str, conversation_id: str, inbound: InboundContext
) -> None:
    access_token = decrypt_access_token(inbound.access_token_encrypted)

    if inbound.billing_gate_step is None:
        await _open_gate(session, tenant_id, conversation_id, inbound, access_token)
    elif inbound.billing_gate_step == "aguardando_selecao_pacote":
        await _handle_package_selection(session, tenant_id, conversation_id, inbound, access_token)
    elif inbound.billing_gate_step == "aguardando_pagamento":
        await _handle_awaiting_payment(session, conversation_id, inbound, access_token)


async def _welcome_text(
    session: AsyncSession, tenant_id: str, contact_phone_number: str, configured: str | None
) -> str:
    if configured:
        return configured
    ja_comprou = await session.scalar(
        select(tables.end_customer_credit_transactions.c.id)
        .where(
            tables.end_customer_credit_transactions.c.tenant_id == uuid.UUID(tenant_id),
            tables.end_customer_credit_transactions.c.contact_phone_number == contact_phone_number,
            tables.end_customer_credit_transactions.c.type == "purchase",
        )
        .limit(1)
    )
    if ja_comprou:
        return "Seus créditos acabaram! Escolha um pacote pra continuar:"
    return "Olá! Escolha um pacote de créditos pra começar o atendimento:"


def _packages_to_sections(packages: list[dict]) -> list[dict]:
    return [
        {
            "title": "Pacotes disponíveis",
            "rows": [
                {
                    "id": p["name"],
                    "title": p["name"],
                    "description": f"R$ {p['price_brl']} = {p['credits_granted']} créditos",
                }
                for p in packages
            ],
        }
    ]


async def _send_package_list(inbound: InboundContext, access_token: str) -> None:
    await send_interactive_list_message(
        phone_number_id=inbound.phone_number_id,
        access_token=access_token,
        to=inbound.contact_phone_number,
        header="Pacotes de créditos",
        body="Escolha uma opção:",
        sections=_packages_to_sections(inbound.end_customer_packages),
    )


async def _open_gate(
    session: AsyncSession,
    tenant_id: str,
    conversation_id: str,
    inbound: InboundContext,
    access_token: str,
) -> None:
    text = await _welcome_text(
        session, tenant_id, inbound.contact_phone_number, inbound.billing_gate_welcome_text
    )
    await send_text_message(
        phone_number_id=inbound.phone_number_id,
        access_token=access_token,
        to=inbound.contact_phone_number,
        text=text,
    )
    await _send_package_list(inbound, access_token)
    await session.execute(
        update(tables.conversations)
        .where(tables.conversations.c.id == uuid.UUID(conversation_id))
        .values(billing_gate_step="aguardando_selecao_pacote", billing_gate_retries=0)
    )
    await session.commit()


def _resolve_package_by_title(packages: list[dict], title: str) -> dict | None:
    for package in packages:
        if package["name"] == title:
            return package
    return None


async def _escalate_to_human(session: AsyncSession, conversation_id: str) -> None:
    await session.execute(
        update(tables.conversations)
        .where(tables.conversations.c.id == uuid.UUID(conversation_id))
        .values(state="human", billing_gate_step=None, billing_gate_retries=0)
    )
    await session.commit()


async def _handle_package_selection(
    session: AsyncSession,
    tenant_id: str,
    conversation_id: str,
    inbound: InboundContext,
    access_token: str,
) -> None:
    package = _resolve_package_by_title(inbound.end_customer_packages, inbound.message_content)
    if package is None:
        retries = inbound.billing_gate_retries + 1
        if retries >= MAX_RETRIES:
            await _escalate_to_human(session, conversation_id)
            return
        await send_text_message(
            phone_number_id=inbound.phone_number_id,
            access_token=access_token,
            to=inbound.contact_phone_number,
            text="Não entendi — escolha uma opção da lista abaixo:",
        )
        await _send_package_list(inbound, access_token)
        await session.execute(
            update(tables.conversations)
            .where(tables.conversations.c.id == uuid.UUID(conversation_id))
            .values(billing_gate_retries=retries)
        )
        await session.commit()
        return

    checkout_url = await create_end_customer_checkout(
        tenant_id=tenant_id,
        contact_phone_number=inbound.contact_phone_number,
        package_id=package["id"],
    )
    await send_text_message(
        phone_number_id=inbound.phone_number_id,
        access_token=access_token,
        to=inbound.contact_phone_number,
        text=f"Aqui está o link de pagamento: {checkout_url}",
    )
    await session.execute(
        update(tables.conversations)
        .where(tables.conversations.c.id == uuid.UUID(conversation_id))
        .values(
            billing_gate_step="aguardando_pagamento",
            billing_gate_checkout_url=checkout_url,
            billing_gate_retries=0,
        )
    )
    await session.commit()


async def _handle_awaiting_payment(
    session: AsyncSession, conversation_id: str, inbound: InboundContext, access_token: str
) -> None:
    retries = inbound.billing_gate_retries + 1
    if retries >= MAX_RETRIES:
        await _escalate_to_human(session, conversation_id)
        return
    await send_text_message(
        phone_number_id=inbound.phone_number_id,
        access_token=access_token,
        to=inbound.contact_phone_number,
        text=(
            "Ainda aguardando a confirmação do pagamento. Aqui está o link de novo: "
            f"{inbound.billing_gate_checkout_url}"
        ),
    )
    await session.execute(
        update(tables.conversations)
        .where(tables.conversations.c.id == uuid.UUID(conversation_id))
        .values(billing_gate_retries=retries)
    )
    await session.commit()
```

- [ ] **Step 5: Rodar e confirmar sucesso**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_billing_gate.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 6: Ligar no `process_inbound_message`**

Em `apps/worker/app/tasks/messages.py`, adicione o import:

```python
from app.billing_gate import handle_billing_gate, maybe_enter_gate
```

E, logo depois de `if inbound is None: return` (antes do `if inbound.conversation_state != "agent":`), adicione:

```python
    if inbound is None:
        return

    async with open_tenant_session(session_factory, tenant_id) as session:
        entrou_no_gate = await maybe_enter_gate(session, tenant_id, conversation_id, inbound)
    if entrou_no_gate:
        async with open_tenant_session(session_factory, tenant_id) as session:
            await handle_billing_gate(session, tenant_id, conversation_id, inbound)
        return

    if inbound.conversation_state != "agent":
```

(A entrada no gate é decidida com o `inbound` já carregado no início da função — se `maybe_enter_gate` acabou de mudar o estado pra `billing_gate` nesta mesma execução, `handle_billing_gate` já processa a mensagem atual dentro do gate imediatamente, sem esperar a próxima — é a mensagem que causou a entrada no gate que também abre o diálogo.)

- [ ] **Step 7: Escrever o teste que falha em `test_process_inbound_message.py`**

Adicione, no fim do arquivo (confira o padrão exato de `_ctx()`/`_inbound()` já usado no restante do arquivo antes de escrever — os helpers já existem, reaproveite):

```python
async def test_entra_no_billing_gate_e_nao_chama_agents(monkeypatch) -> None:
    entrada_mock = AsyncMock(return_value=True)
    handle_mock = AsyncMock()
    monkeypatch.setattr(messages_task, "maybe_enter_gate", entrada_mock)
    monkeypatch.setattr(messages_task, "handle_billing_gate", handle_mock)
    ctx = _ctx()
    session = AsyncMock()
    ctx["session_factory"].return_value.__aenter__ = AsyncMock(return_value=session)

    monkeypatch.setattr(
        messages_task,
        "_load_context",
        AsyncMock(
            return_value=_inbound(state="agent", credit_balance=1000)
        ),
    )

    await process_inbound_message(ctx, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    entrada_mock.assert_awaited_once()
    handle_mock.assert_awaited_once()
    ctx["http"].post.assert_not_called()
```

(Confira o nome exato do parâmetro de estado em `_inbound(...)` já existente no arquivo — pode ser `state=` ou `conversation_state=`, use o que já está definido no helper local, sem inventar um novo.)

- [ ] **Step 8: Rodar e confirmar a falha, depois o sucesso**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_process_inbound_message.py -v`
Expected: FAIL antes do Step 6 (a função não existe ainda no módulo pra ser monkeypatchada) — depois de aplicado o Step 6, PASS.

- [ ] **Step 9: Rodar a suíte completa do worker + lint**

Run: `cd apps/worker && python3 -m pytest tests/unit -q && python3 -m ruff check app/billing_gate.py app/tasks/messages.py`
Expected: todos passam, sem erro novo nos 2 arquivos.

- [ ] **Step 10: Commit**

```bash
git add apps/worker/app/billing_gate.py apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_billing_gate.py apps/worker/tests/unit/test_process_inbound_message.py
git commit -m "feat(worker): máquina de estados do billing gate determinístico, ligada em process_inbound_message"
```

---

### Task 7: Webhook Stripe do tenant — transição condicional

**Files:**
- Modify: `apps/api/app/services/end_customer_billing.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_service.py`

**Interfaces:**
- Consumes: `Conversation.billing_gate_step`/`billing_gate_retries`/`billing_gate_checkout_url` (Task 2), `TenantBillingSettings.insufficient_balance_policy` (já existente).
- Produces: nada consumido por outra task deste plano — última task.

- [ ] **Step 1: Atualizar os 3 testes existentes afetados**

Em `apps/api/tests/unit/test_end_customer_billing_service.py`, a nova consulta de `insufficient_balance_policy` entra na posição 4 do `session.scalar.side_effect` (depois de `already_processed`, `package`, `balance`; antes de `conversation`, `number`). Troque:

```python
    async def test_credita_saldo_novo_e_manda_confirmacao(self, session, arq, monkeypatch) -> None:
        package = _package()
        conversation = _conversation()
        number = _number()
        session.scalar = AsyncMock(side_effect=[None, package, None, conversation, number])
```

por:

```python
    async def test_credita_saldo_novo_e_manda_confirmacao(self, session, arq, monkeypatch) -> None:
        package = _package()
        conversation = _conversation()
        number = _number()
        session.scalar = AsyncMock(
            side_effect=[None, package, None, "block_with_message", conversation, number]
        )
```

Troque:

```python
    async def test_credita_saldo_existente_soma(self, session, arq, monkeypatch) -> None:
        package = _package()
        existing_balance = SimpleNamespace(
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            credit_balance=100,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.scalar = AsyncMock(side_effect=[None, package, existing_balance, None, None])
```

por:

```python
    async def test_credita_saldo_existente_soma(self, session, arq, monkeypatch) -> None:
        package = _package()
        existing_balance = SimpleNamespace(
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            credit_balance=100,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.scalar = AsyncMock(
            side_effect=[None, package, existing_balance, "block_with_message", None, None]
        )
```

Troque:

```python
    async def test_falha_ao_confirmar_via_whatsapp_nao_impede_credito(
        self, session, arq, monkeypatch
    ) -> None:
        package = _package()
        session.scalar = AsyncMock(side_effect=[None, package, None, None, None])
```

por:

```python
    async def test_falha_ao_confirmar_via_whatsapp_nao_impede_credito(
        self, session, arq, monkeypatch
    ) -> None:
        package = _package()
        session.scalar = AsyncMock(
            side_effect=[None, package, None, "block_with_message", None, None]
        )
```

- [ ] **Step 2: Escrever o teste novo que falha**

Adicione, no fim de `class TestProcessEndCustomerCheckoutCompleted`:

```python
    async def test_transiciona_billing_gate_para_agent_quando_deterministic_gate(
        self, session, arq, monkeypatch
    ) -> None:
        package = _package()
        conversation = _conversation(
            state="billing_gate", billing_gate_step="aguardando_pagamento", billing_gate_retries=1
        )
        number = _number()
        session.scalar = AsyncMock(
            side_effect=[None, package, None, "deterministic_gate", conversation, number]
        )
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        assert conversation.state == "agent"
        assert conversation.billing_gate_step is None
        assert conversation.billing_gate_retries == 0
        arq.enqueue_job.assert_not_called()

    async def test_nao_transiciona_conversa_em_human_mesmo_com_deterministic_gate(
        self, session, arq, monkeypatch
    ) -> None:
        package = _package()
        conversation = _conversation(state="human")
        number = _number()
        session.scalar = AsyncMock(
            side_effect=[None, package, None, "deterministic_gate", conversation, number]
        )
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        assert conversation.state == "human"
```

- [ ] **Step 3: Rodar e confirmar a falha**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py -v`
Expected: FAIL — os 2 testes novos (a transição não existe ainda) e os 3 testes atualizados (o novo item no `side_effect` não é consumido pelo código atual, então `conversation`/`number` recebem os valores errados — vai falhar com erro de atributo ou asserção incorreta, não silenciosamente).

- [ ] **Step 4: Implementar a transição condicional**

Em `apps/api/app/services/end_customer_billing.py`, troque:

```python
    await session.commit()

    await _send_purchase_confirmation(session, tenant_id, contact_phone_number, arq)
```

por:

```python
    await session.commit()

    policy = await session.scalar(
        select(TenantBillingSettings.insufficient_balance_policy).where(
            TenantBillingSettings.tenant_id == tenant_id
        )
    )
    await _send_purchase_confirmation(
        session, tenant_id, contact_phone_number, arq, policy or "block_with_message"
    )
```

(precisa importar `TenantBillingSettings` — já está na lista de imports de `app.models` no topo do arquivo, confirme antes de adicionar de novo.)

Troque a assinatura e o corpo de `_send_purchase_confirmation`:

```python
async def _send_purchase_confirmation(
    session: AsyncSession, tenant_id: uuid.UUID, contact_phone_number: str, arq: ArqRedis
) -> None:
    """Best-effort: uma falha ao mandar a confirmação não desfaz o crédito
    já commitado acima — o cliente só não recebe o aviso, mas o saldo está lá.

    Além do aviso instantâneo (fixo, via WhatsApp direto), também aciona o
    próprio agente com uma mensagem de sistema avisando que o pagamento foi
    concluído — mesma fila (process_inbound_message) que o webhook do
    WhatsApp usa. Isso faz a Sofia reagir e efetivar a transferência sozinha,
    sem depender do cliente digitar "já paguei" — ela nunca via a mensagem de
    confirmação, porque essa mensagem era mandada direto pro WhatsApp, sem
    nunca entrar na memória (checkpoint) do agente.
    """
    try:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.contact_phone_number == contact_phone_number,
            )
        )
        number = await session.scalar(
            select(WhatsAppNumber).where(
                WhatsAppNumber.tenant_id == tenant_id, WhatsAppNumber.status == "connected"
            )
        )
        if number is None or conversation is None:
            logger.warning(
                "Sem número/conversa pra confirmar pagamento | tenant=%s contato=%s",
                tenant_id,
                contact_phone_number,
            )
            return

        await send_text_message(
            phone_number_id=number.phone_number_id,
            access_token=decrypt_access_token(number.access_token_encrypted),
            to=contact_phone_number,
            text="Pagamento confirmado! Você já pode continuar a conversa.",
        )

        session.add(
            Message(
                conversation_id=conversation.id,
                tenant_id=tenant_id,
                sender_type="system",
                content="Pagamento confirmado! Você já pode continuar a conversa.",
                delivery_status="sent",
            )
        )

        trigger_message = Message(
            conversation_id=conversation.id,
            tenant_id=tenant_id,
            sender_type="system",
            content=(
                "O cliente concluiu o pagamento do pacote de créditos com sucesso"
                " — saldo já disponível."
            ),
        )
        session.add(trigger_message)

        conversation.last_message_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(trigger_message)

        await arq.enqueue_job(
            "process_inbound_message",
            tenant_id=str(tenant_id),
            conversation_id=str(conversation.id),
            message_id=str(trigger_message.id),
        )
    except WhatsAppSendError:
        logger.exception(
            "Falha ao confirmar pagamento via WhatsApp | tenant=%s contato=%s",
            tenant_id,
            contact_phone_number,
        )
    except Exception:
        logger.exception(
            "Erro inesperado ao confirmar pagamento | tenant=%s contato=%s",
            tenant_id,
            contact_phone_number,
        )
```

por:

```python
async def _send_purchase_confirmation(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    contact_phone_number: str,
    arq: ArqRedis,
    insufficient_balance_policy: str,
) -> None:
    """Best-effort: uma falha ao mandar a confirmação não desfaz o crédito
    já commitado acima — o cliente só não recebe o aviso, mas o saldo está lá.

    Além do aviso instantâneo (fixo, via WhatsApp direto), o comportamento
    depois disso depende de insufficient_balance_policy (rollout gradual do
    billing gate determinístico, ver
    docs/superpowers/specs/2026-07-22-billing-gate-deterministico-design.md):
    - "block_with_message" (default, tenant ainda não migrado): aciona o
      próprio agente com uma mensagem de sistema avisando que o pagamento
      foi concluído — mesma fila (process_inbound_message) que o webhook do
      WhatsApp usa. Isso faz a Sofia reagir e efetivar a transferência
      sozinha, sem depender do cliente digitar "já paguei".
    - "deterministic_gate" (tenant migrado): a conversa, se estiver em
      billing_gate, volta direto pra "agent" — sem acionar o agents, já que
      o checkpoint do LangGraph nunca foi tocado por essa mudança de estado
      e a conversa retoma de onde estava (ou começa do zero pelo ponto de
      entrada, se nunca tinha sido atendida).
    """
    try:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.contact_phone_number == contact_phone_number,
            )
        )
        number = await session.scalar(
            select(WhatsAppNumber).where(
                WhatsAppNumber.tenant_id == tenant_id, WhatsAppNumber.status == "connected"
            )
        )
        if number is None or conversation is None:
            logger.warning(
                "Sem número/conversa pra confirmar pagamento | tenant=%s contato=%s",
                tenant_id,
                contact_phone_number,
            )
            return

        await send_text_message(
            phone_number_id=number.phone_number_id,
            access_token=decrypt_access_token(number.access_token_encrypted),
            to=contact_phone_number,
            text="Pagamento confirmado! Você já pode continuar a conversa.",
        )

        session.add(
            Message(
                conversation_id=conversation.id,
                tenant_id=tenant_id,
                sender_type="system",
                content="Pagamento confirmado! Você já pode continuar a conversa.",
                delivery_status="sent",
            )
        )
        conversation.last_message_at = datetime.now(UTC)

        if insufficient_balance_policy == "deterministic_gate":
            if conversation.state == "billing_gate":
                conversation.state = "agent"
                conversation.billing_gate_step = None
                conversation.billing_gate_retries = 0
            await session.commit()
            return

        trigger_message = Message(
            conversation_id=conversation.id,
            tenant_id=tenant_id,
            sender_type="system",
            content=(
                "O cliente concluiu o pagamento do pacote de créditos com sucesso"
                " — saldo já disponível."
            ),
        )
        session.add(trigger_message)
        await session.commit()
        await session.refresh(trigger_message)

        await arq.enqueue_job(
            "process_inbound_message",
            tenant_id=str(tenant_id),
            conversation_id=str(conversation.id),
            message_id=str(trigger_message.id),
        )
    except WhatsAppSendError:
        logger.exception(
            "Falha ao confirmar pagamento via WhatsApp | tenant=%s contato=%s",
            tenant_id,
            contact_phone_number,
        )
    except Exception:
        logger.exception(
            "Erro inesperado ao confirmar pagamento | tenant=%s contato=%s",
            tenant_id,
            contact_phone_number,
        )
```

- [ ] **Step 5: Rodar e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 6: Rodar a suíte completa + lint**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v && uv run ruff check .`
Expected: todos passam (ou skip nos de integração que exigem Postgres real), lint limpo.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/end_customer_billing.py apps/api/tests/unit/test_end_customer_billing_service.py
git commit -m "feat(api): transição billing_gate->agent condicional por insufficient_balance_policy, sem remover o caminho antigo"
```

---

## Residual explicitamente fora de escopo (não esquecer depois)

- **Frontend**: `apps/web` não sabe renderizar `state: "billing_gate"` de forma dedicada — o tipo `ConversationState` (`apps/web/src/lib/types.ts`) continua `"agent" | "human"`. Uma conversa em `billing_gate` hoje seria tratada como "não humana" nos componentes que só checam `state === "human"` (ex: badge de atendimento manual) — sem crash, mas sem indicação visual dedicada. Etapa futura, não deste plano.
- **Migração dos tenants** (mudar `insufficient_balance_policy` de `block_with_message` pra `deterministic_gate`) é operacional — update direto no banco por tenant, sem endpoint/UI. Não faz parte deste plano.
- **Etapa 5** (remoção do gate antigo do `apps/agents`) só acontece depois de 100% dos tenants migrados — fora de escopo deste plano por definição.
