# Takeover repensado + contexto no LangGraph — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mensagens do takeover humano entram no checkpoint do LangGraph; a IA reassume sozinha por timeout de presença; focar o composer assume a conversa automaticamente com popup lateral.

**Architecture:** Novo endpoint `POST /conversations/{thread_id}/context` no `agents` anexa mensagens ao checkpoint via `aupdate_state` (sem LLM/débito). O `api` (resposta do atendente) e o `worker` (mensagem do contato em modo `human` ou saldo esgotado) chamam esse endpoint best-effort. Presença = heartbeat do painel (`human_last_seen_at`); o worker reverte pra `agent` lazy quando o timeout expira, na chegada da próxima mensagem. No front, o composer fica sempre ativo e o foco dispara o takeover.

**Tech Stack:** FastAPI + LangGraph/AsyncPostgresSaver (agents), FastAPI + SQLAlchemy + Alembic (api), Arq + SQLAlchemy Core (worker), Next.js 15 + Vitest (web).

**Spec:** `docs/superpowers/specs/2026-07-15-takeover-contexto-langgraph-design.md`

## Global Constraints

- Roles do context: `"contact"` → `HumanMessage`, `"attendant"` → `AIMessage`. Nunca outro mapeamento.
- Context nunca roda o grafo, nunca chama LLM, nunca debita crédito.
- Sync é **best-effort** em todos os call sites: falha → `logger.warning`, nunca quebra a operação principal.
- Migration é a **`0010`** (`down_revision = "0009"`) — a 0009 já existe (end_customer_billing).
- Timeout: env `HUMAN_TAKEOVER_TIMEOUT_SECONDS` no worker, default `180`. `human_last_seen_at` NULL = expirado.
- Texto do popup: título "IA pausada", corpo "Você assumiu esta conversa. A IA reassume após 3 minutos sem atividade.", botões "Devolver pra IA" e "Fechar".
- Comandos: agents/api/worker → `cd apps/<app> && uv run pytest tests/unit -q` e `uv run ruff check . && uv run ruff format --check .`; web → `cd apps/web && pnpm test` e `pnpm lint`.
- `apps/agents/API_AGENTS.md` é fonte da verdade do agents — atualizar na Task 1.

---

### Task 1: `agents` — endpoint de contexto

**Files:**
- Create: `apps/agents/services/update_context.py`
- Modify: `apps/agents/api/routes.py` (imports, 2 models, 1 route)
- Modify: `apps/agents/API_AGENTS.md` (nova seção de endpoint)
- Test: `apps/agents/tests/unit/test_update_context.py` (novo), `apps/agents/tests/unit/test_routes.py`

**Interfaces:**
- Consumes: `graph` (`agents.workflow`), `DB_URI` (`services.call_agent`), `verify_api_key` (existe em routes.py).
- Produces: `async def add_context_messages(thread_id: str, messages: list[dict], db_uri: str = DB_URI) -> int`; rota `POST /conversations/{thread_id}/context` com body `{"messages": [{"role": "contact"|"attendant", "content": str}]}` → `{"added": n}`. Tasks 3 e 4 consomem essa rota.

- [ ] **Step 1: Testes do service (falhando)**

Criar `apps/agents/tests/unit/test_update_context.py`:

```python
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage

import services.update_context as update_context_module
from services.update_context import add_context_messages


def _mock_checkpointer(monkeypatch):
    checkpointer = MagicMock()
    checkpointer.setup = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=checkpointer)
    cm.__aexit__ = AsyncMock(return_value=False)
    saver_cls = MagicMock()
    saver_cls.from_conn_string = MagicMock(return_value=cm)
    monkeypatch.setattr(update_context_module, "AsyncPostgresSaver", saver_cls)

    agent = MagicMock()
    agent.aupdate_state = AsyncMock()
    graph = MagicMock()
    graph.compile = MagicMock(return_value=agent)
    monkeypatch.setattr(update_context_module, "graph", graph)
    return agent


async def test_mapeia_roles_e_anexa_ao_checkpoint(monkeypatch):
    agent = _mock_checkpointer(monkeypatch)

    added = await add_context_messages(
        "tenant-1:5511999999999",
        [
            {"role": "contact", "content": "oi, ainda tá aí?"},
            {"role": "attendant", "content": "sim! sou o Dr. Silva, vou te ajudar"},
        ],
        db_uri="postgresql://x",
    )

    assert added == 2
    agent.aupdate_state.assert_awaited_once()
    config, values = agent.aupdate_state.await_args.args
    assert config == {"configurable": {"thread_id": "tenant-1:5511999999999"}}
    lc_messages = values["messages"]
    assert isinstance(lc_messages[0], HumanMessage)
    assert lc_messages[0].content == "oi, ainda tá aí?"
    assert isinstance(lc_messages[1], AIMessage)
    assert lc_messages[1].content == "sim! sou o Dr. Silva, vou te ajudar"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/agents && uv run pytest tests/unit/test_update_context.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.update_context'`.

- [ ] **Step 3: Implementar o service**

Criar `apps/agents/services/update_context.py`:

```python
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from loguru import logger

from agents.workflow import graph
from services.call_agent import DB_URI

# contact = o cliente final (HumanMessage); attendant = o atendente do
# escritório falando pelo "nosso lado" (AIMessage) — assim, quando a IA
# reassume, o histórico dela reflete quem disse o quê.
ROLE_TO_MESSAGE = {"contact": HumanMessage, "attendant": AIMessage}


async def add_context_messages(
    thread_id: str, messages: list[dict], db_uri: str = DB_URI
) -> int:
    """Anexa mensagens ao checkpoint sem rodar o grafo (sem LLM, sem débito).

    Mantém a memória do agente durante o takeover humano — aupdate_state usa
    o reducer add_messages do estado, só acrescentando ao histórico.
    """
    lc_messages = [ROLE_TO_MESSAGE[m["role"]](content=m["content"]) for m in messages]
    config = {"configurable": {"thread_id": thread_id}}
    async with AsyncPostgresSaver.from_conn_string(db_uri) as checkpointer:
        await checkpointer.setup()
        agent = graph.compile(checkpointer=checkpointer)
        await agent.aupdate_state(config, {"messages": lc_messages})
    logger.info(
        "Contexto anexado ao checkpoint | thread_id={} | mensagens={}",
        thread_id,
        len(lc_messages),
    )
    return len(lc_messages)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/agents && uv run pytest tests/unit/test_update_context.py -q`
Expected: PASS.

- [ ] **Step 5: Testes da rota (falhando)**

Adicionar ao final de `apps/agents/tests/unit/test_routes.py`:

```python
CONTEXT_PAYLOAD = {
    "messages": [
        {"role": "contact", "content": "oi"},
        {"role": "attendant", "content": "olá, sou o atendente"},
    ]
}


def test_context_anexa_mensagens_e_retorna_added(client, monkeypatch):
    add_mock = AsyncMock(return_value=2)
    monkeypatch.setattr(routes, "add_context_messages", add_mock)

    response = client.post("/conversations/t1:5511/context", json=CONTEXT_PAYLOAD)

    assert response.status_code == 200
    assert response.json() == {"added": 2}
    add_mock.assert_awaited_once_with(
        "t1:5511",
        [
            {"role": "contact", "content": "oi"},
            {"role": "attendant", "content": "olá, sou o atendente"},
        ],
    )


def test_context_com_messages_vazio_retorna_422(client):
    response = client.post("/conversations/t1:5511/context", json={"messages": []})
    assert response.status_code == 422


def test_context_com_role_invalido_retorna_422(client):
    payload = {"messages": [{"role": "robo", "content": "oi"}]}
    response = client.post("/conversations/t1:5511/context", json=payload)
    assert response.status_code == 422


def test_context_erro_interno_retorna_500(client, monkeypatch):
    add_mock = AsyncMock(side_effect=RuntimeError("checkpoint fora do ar"))
    monkeypatch.setattr(routes, "add_context_messages", add_mock)

    response = client.post("/conversations/t1:5511/context", json=CONTEXT_PAYLOAD)

    assert response.status_code == 500


def test_context_exige_api_key(client, monkeypatch):
    monkeypatch.setattr(routes, "AGENTS_API_KEY", "chave-secreta")
    response = client.post("/conversations/t1:5511/context", json=CONTEXT_PAYLOAD)
    assert response.status_code == 403
```

- [ ] **Step 6: Rodar e ver falhar**

Run: `cd apps/agents && uv run pytest tests/unit/test_routes.py -q`
Expected: FAIL — 404 nos testes novos (rota não existe) e `AttributeError` no monkeypatch de `add_context_messages`.

- [ ] **Step 7: Implementar a rota**

Em `apps/agents/api/routes.py`:

Adicionar aos imports (topo do arquivo):

```python
from typing import Literal

from services.update_context import add_context_messages
```

E `Field` já vem de `pydantic` (linha existente `from pydantic import BaseModel, Field`).

Adicionar os models após `SummaryRequest`:

```python
class ContextMessageIn(BaseModel):
    role: Literal["contact", "attendant"]
    content: str


class ContextRequest(BaseModel):
    messages: list[ContextMessageIn] = Field(min_length=1)
```

Adicionar a rota após `delete_conversation`:

```python
@app.post("/conversations/{thread_id}/context", dependencies=[Depends(verify_api_key)])
async def add_context(thread_id: str, body: ContextRequest):
    """Anexa mensagens do takeover humano ao checkpoint — sem rodar o grafo.

    Chamado pelo api (resposta do atendente) e pelo worker (mensagem do
    contato em modo human/saldo esgotado). Sem LLM, sem débito de créditos.
    """
    try:
        added = await add_context_messages(
            thread_id,
            [{"role": m.role, "content": m.content} for m in body.messages],
        )
    except Exception:
        logger.exception("Erro ao anexar contexto | thread_id={}", thread_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao anexar contexto.",
        )
    return {"added": added}
```

- [ ] **Step 8: Rodar e ver passar + suíte e lint**

Run: `cd apps/agents && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS (suíte completa) e lint limpo.

- [ ] **Step 9: Documentar em API_AGENTS.md**

Ler `apps/agents/API_AGENTS.md`, localizar a seção que documenta `DELETE /conversations/{thread_id}` e inserir logo APÓS ela (antes da próxima seção de endpoint) esta seção, adaptando o nível de heading ao padrão do arquivo:

```markdown
### POST /conversations/{thread_id}/context

Anexa mensagens ao checkpoint do LangGraph **sem rodar o grafo** (sem LLM, sem
débito de créditos). Usado pra manter a memória do agente durante o takeover
humano: quando a IA reassume, o histórico contém o que atendente e contato
conversaram.

- Auth: mesma API key de serviço (`Authorization: <AGENTS_API_KEY>`).
- Body: `{"messages": [{"role": "contact" | "attendant", "content": "..."}]}`
  (mínimo 1 mensagem).
- Mapeamento: `contact` → `HumanMessage`; `attendant` → `AIMessage` (o
  atendente fala pelo escritório).
- Resposta: `200 {"added": <n>}`. `messages` vazio ou `role` inválido → 422.
  Falha de checkpoint → 500.
- Implementação: `services/update_context.py` (`aupdate_state` com o reducer
  `add_messages`).
```

- [ ] **Step 10: Commit**

```bash
git add apps/agents/services/update_context.py apps/agents/api/routes.py apps/agents/API_AGENTS.md apps/agents/tests/unit/test_update_context.py apps/agents/tests/unit/test_routes.py
git commit -m "feat(agents): POST /conversations/{thread_id}/context anexa takeover ao checkpoint"
```

---

### Task 2: `api` — migration 0010, heartbeat e `human_last_seen_at`

**Files:**
- Create: `apps/api/alembic/versions/0010_conversation_human_last_seen.py`
- Modify: `apps/api/app/models/conversation.py` (nova coluna)
- Modify: `apps/api/app/api/v1/conversations.py` (PATCH seta timestamp; rota heartbeat)
- Test: `apps/api/tests/unit/test_conversations_routes.py`

**Interfaces:**
- Consumes: `_get_conversation`, `get_tenant_session`, `get_current_tenant` (existem).
- Produces: coluna `conversations.human_last_seen_at` (timestamptz nullable — Task 4 lê via SQLAlchemy Core no worker); rota `POST /api/v1/conversations/{id}/heartbeat` → 204 (Task 5 consome).

- [ ] **Step 1: Testes (falhando)**

Abrir `apps/api/tests/unit/test_conversations_routes.py`, estudar o padrão de fixtures existente (session mockada + dependency overrides, mesmo desenho de `test_whatsapp_connection_routes.py`) e adicionar, seguindo esse padrão:

```python
class TestHeartbeat:
    def test_seta_human_last_seen_at_e_retorna_204(self, client, session) -> None:
        conversation = _conversation(state="human")
        conversation.human_last_seen_at = None
        session.scalar.return_value = conversation

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/heartbeat")

        assert response.status_code == 204
        assert conversation.human_last_seen_at is not None
        session.commit.assert_awaited()

    def test_conversa_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/heartbeat")

        assert response.status_code == 404


class TestPatchSetaPresenca:
    def test_virar_human_seta_human_last_seen_at(self, client, session) -> None:
        conversation = _conversation(state="agent")
        conversation.human_last_seen_at = None
        session.scalar.return_value = conversation

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}", json={"state": "human"}
        )

        assert response.status_code == 200
        assert conversation.human_last_seen_at is not None

    def test_virar_agent_nao_seta_timestamp(self, client, session) -> None:
        conversation = _conversation(state="human")
        conversation.human_last_seen_at = None
        session.scalar.return_value = conversation

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}", json={"state": "agent"}
        )

        assert response.status_code == 200
        assert conversation.human_last_seen_at is None
```

Notas de adaptação obrigatórias: usar o helper/factory de conversa que o arquivo já tem (se chamar diferente de `_conversation`, seguir o nome existente e só garantir o atributo `human_last_seen_at` no objeto); usar a constante de id existente (`CONVERSATION_ID` ou equivalente do arquivo). Se o factory usa `SimpleNamespace`, adicionar `human_last_seen_at=None` ao construtor dele em vez de setar depois.

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py -q`
Expected: FAIL — 404/405 no heartbeat (rota não existe); asserts de timestamp falhando no PATCH.

- [ ] **Step 3: Migration + modelo**

Criar `apps/api/alembic/versions/0010_conversation_human_last_seen.py`:

```python
"""human_last_seen_at em conversations

Presença do atendente no takeover: atualizado pelo heartbeat do painel e
pelo PATCH pra human; o worker compara com HUMAN_TAKEOVER_TIMEOUT_SECONDS
pra reverter a conversa pra agent quando o atendente some (reversão lazy,
na chegada da próxima mensagem do contato).

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-15
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("human_last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "human_last_seen_at")
```

Em `apps/api/app/models/conversation.py`, adicionar após `summary_generated_at`:

```python
    human_last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 4: Rotas**

Em `apps/api/app/api/v1/conversations.py`:

No `update_state` (PATCH), substituir o corpo:

```python
    conversation = await _get_conversation(conversation_id, ctx, session)
    conversation.state = body.state
    if body.state == "human":
        # Takeover começa "presente" — o heartbeat do painel mantém depois.
        conversation.human_last_seen_at = datetime.now(UTC)
    await session.commit()
    return ConversationOut.model_validate(conversation)
```

Adicionar a rota após `update_state`:

```python
@router.post("/{conversation_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def heartbeat(
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Presença do atendente: o painel envia a cada ciclo de polling enquanto
    a conversa está aberta em modo human. O worker usa human_last_seen_at pra
    decidir se a IA reassume (timeout)."""
    conversation = await _get_conversation(conversation_id, ctx, session)
    conversation.human_last_seen_at = datetime.now(UTC)
    await session.commit()
```

- [ ] **Step 5: Rodar e ver passar + suíte e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS e lint limpo.

- [ ] **Step 6: Commit**

```bash
git add apps/api/alembic/versions/0010_conversation_human_last_seen.py apps/api/app/models/conversation.py apps/api/app/api/v1/conversations.py apps/api/tests/unit/test_conversations_routes.py
git commit -m "feat(api): heartbeat de presença do takeover (human_last_seen_at, migration 0010)"
```

---

### Task 3: `api` — sync da resposta do atendente

**Files:**
- Modify: `apps/api/app/clients/agents.py` (nova função)
- Modify: `apps/api/app/api/v1/conversations.py` (`send_message` chama o sync best-effort)
- Test: `apps/api/tests/unit/test_conversations_routes.py`

**Interfaces:**
- Consumes: rota `POST /conversations/{thread_id}/context` do agents (Task 1); `AgentsNetworkError`/`AgentsApiError`/`_auth_headers` (existem em `clients/agents.py`).
- Produces: `async def sync_conversation_context(*, tenant_id: str, contact_phone_number: str, role: str, content: str) -> None`.

- [ ] **Step 1: Testes (falhando)**

Adicionar em `apps/api/tests/unit/test_conversations_routes.py` (na classe/área dos testes de `send_message`, seguindo os mocks existentes do arquivo — que já mockam `send_text_message`/`decrypt_access_token` via monkeypatch no módulo `conversations`):

```python
    def test_resposta_humana_sincroniza_contexto_com_agents(
        self, client, session, monkeypatch
    ) -> None:
        sync_mock = AsyncMock()
        monkeypatch.setattr(conversations_module, "sync_conversation_context", sync_mock)
        # ... setup existente de conversa em modo human + número conectado ...

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages",
            json={"content": "olá, aqui é o Dr. Silva"},
        )

        assert response.status_code == 201
        sync_mock.assert_awaited_once()
        kwargs = sync_mock.await_args.kwargs
        assert kwargs["role"] == "attendant"
        assert kwargs["content"] == "olá, aqui é o Dr. Silva"

    def test_falha_no_sync_nao_quebra_o_envio(self, client, session, monkeypatch) -> None:
        sync_mock = AsyncMock(side_effect=AgentsNetworkError("agents fora do ar"))
        monkeypatch.setattr(conversations_module, "sync_conversation_context", sync_mock)
        # ... mesmo setup ...

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages",
            json={"content": "olá"},
        )

        assert response.status_code == 201
```

Nota: `conversations_module` = `import app.api.v1.conversations as conversations_module` (adicionar o import se o arquivo ainda não tem); reaproveitar o setup dos testes de `send_message` existentes (conversa `human`, `WhatsAppNumber` mockado, `send_text_message` mockado). `AgentsNetworkError` importa de `app.clients.agents`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py -q`
Expected: FAIL — `AttributeError: sync_conversation_context` no monkeypatch.

- [ ] **Step 3: Client**

Ao final de `apps/api/app/clients/agents.py`:

```python
_CONTEXT_TIMEOUT_SECONDS = 15


async def sync_conversation_context(
    *, tenant_id: str, contact_phone_number: str, role: str, content: str
) -> None:
    """POST /conversations/{thread_id}/context — anexa uma mensagem do takeover
    ao checkpoint do LangGraph (sem LLM, sem débito). Levanta AgentsNetworkError/
    AgentsApiError; o call site decide se é best-effort."""
    thread_id = f"{tenant_id}:{contact_phone_number}"
    payload = {"messages": [{"role": role, "content": content}]}
    try:
        async with httpx.AsyncClient(
            base_url=settings.agents_service_url, timeout=_CONTEXT_TIMEOUT_SECONDS
        ) as client:
            response = await client.post(
                f"/conversations/{thread_id}/context", json=payload, headers=_auth_headers()
            )
    except httpx.HTTPError as exc:
        raise AgentsNetworkError(f"Falha de rede ao sincronizar contexto: {exc}") from exc

    if response.is_error:
        logger.warning(
            "agents retornou erro no sync de contexto | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise AgentsApiError(f"agents retornou {response.status_code} no sync de contexto")
```

- [ ] **Step 4: Call site no send_message**

Em `apps/api/app/api/v1/conversations.py`:

Adicionar aos imports: `import logging` (topo, se ausente) e atualizar a linha de import do client:

```python
from app.clients.agents import (
    AgentsApiError,
    AgentsNetworkError,
    generate_conversation_summary,
    sync_conversation_context,
)
```

E após os imports: `logger = logging.getLogger(__name__)` (se ausente).

No `send_message`, logo antes do `return MessageOut.model_validate(message)`:

```python
    try:
        await sync_conversation_context(
            tenant_id=str(ctx.tenant_id),
            contact_phone_number=conversation.contact_phone_number,
            role="attendant",
            content=body.content,
        )
    except (AgentsNetworkError, AgentsApiError) as exc:
        # Best-effort: a mensagem já foi entregue ao contato — sem o sync o
        # agente fica com um buraco de memória, mas a operação não falha.
        logger.warning(
            "Falha ao sincronizar contexto do takeover | conversation=%s erro=%s",
            conversation_id,
            exc,
        )
```

- [ ] **Step 5: Rodar e ver passar + suíte e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS e lint limpo.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/clients/agents.py apps/api/app/api/v1/conversations.py apps/api/tests/unit/test_conversations_routes.py
git commit -m "feat(api): resposta do atendente sincroniza contexto no checkpoint do agents"
```

---

### Task 4: `worker` — timeout de presença e sync nos branches de silêncio

**Files:**
- Modify: `apps/worker/app/config.py` (env nova)
- Modify: `apps/worker/app/clients/agents.py` (nova função)
- Modify: `apps/worker/app/tasks/messages.py` (InboundContext, `_load_context`, branches)
- Test: `apps/worker/tests/unit/test_process_inbound_message.py`

**Interfaces:**
- Consumes: rota context do agents (Task 1); coluna `human_last_seen_at` (Task 2); `open_tenant_session`, `tables.conversations` (existem).
- Produces: `async def sync_context_to_agents(http, *, tenant_id: str, contact_phone_number: str, role: str, content: str) -> None`; setting `human_takeover_timeout_seconds: int = 180`.

- [ ] **Step 1: Testes (falhando)**

Em `apps/worker/tests/unit/test_process_inbound_message.py`:

1. Atualizar `_inbound` pra aceitar presença (parâmetro novo com default no FINAL, sem deslocar os existentes):

```python
def _inbound(
    state: str = "agent",
    credit_balance: int = 1000,
    human_last_seen_at=None,
) -> InboundContext:
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
        human_last_seen_at=human_last_seen_at,
    )
```

(Conferir a assinatura atual do helper no arquivo — ele já passa os campos de end_customer_billing; só ADICIONAR `human_last_seen_at`.)

2. No fixture `patched`, adicionar o mock do sync:

```python
        "sync": AsyncMock(),
```

e o monkeypatch correspondente:

```python
    monkeypatch.setattr(messages_task, "sync_context_to_agents", mocks["sync"])
```

3. Atualizar `test_human_state_skips_agent` (o comportamento com `human_last_seen_at=None` agora é EXPIRADO — IA reassume): trocar o setup pra presença recente:

```python
async def test_human_state_skips_agent(patched) -> None:
    patched["load"].return_value = _inbound(
        state="human", human_last_seen_at=datetime.now(UTC)
    )

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_not_awaited()
    patched["persist"].assert_not_awaited()
```

(Adicionar `from datetime import UTC, datetime, timedelta` aos imports do teste.)

4. Testes novos:

```python
async def test_human_nao_expirado_sincroniza_contexto(patched) -> None:
    patched["load"].return_value = _inbound(
        state="human", human_last_seen_at=datetime.now(UTC)
    )

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["sync"].assert_awaited_once()
    kwargs = patched["sync"].await_args.kwargs
    assert kwargs["role"] == "contact"
    assert kwargs["content"] == "Olá"
    patched["send"].assert_not_awaited()


async def test_human_expirado_reativa_ia_e_chama_agente(patched) -> None:
    patched["load"].return_value = _inbound(
        state="human", human_last_seen_at=datetime.now(UTC) - timedelta(seconds=999)
    )
    ctx = _ctx()

    await process_inbound_message(ctx, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    session = ctx["session_factory"].return_value.__aenter__.return_value
    session.execute.assert_awaited()  # UPDATE state='agent'
    patched["send"].assert_awaited_once()
    patched["persist"].assert_awaited_once()


async def test_human_sem_last_seen_e_tratado_como_expirado(patched) -> None:
    patched["load"].return_value = _inbound(state="human", human_last_seen_at=None)

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()


async def test_saldo_esgotado_sincroniza_contexto(patched) -> None:
    patched["load"].return_value = _inbound(credit_balance=0)

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["sync"].assert_awaited_once()
    patched["send"].assert_not_awaited()


async def test_falha_no_sync_nao_quebra(patched) -> None:
    patched["sync"].side_effect = httpx.ConnectError("agents fora do ar")
    patched["load"].return_value = _inbound(
        state="human", human_last_seen_at=datetime.now(UTC)
    )

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)
    # não levanta — best-effort
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/worker && uv run pytest tests/unit/test_process_inbound_message.py -q`
Expected: FAIL — `TypeError` no `_inbound` (campo inexistente em InboundContext) e `AttributeError: sync_context_to_agents`.

- [ ] **Step 3: Settings + client**

Em `apps/worker/app/config.py`, após `credit_tokens_per_credit`:

```python
    # Takeover humano: sem heartbeat do painel há mais que N segundos, a IA
    # reassume a conversa na chegada da próxima mensagem do contato.
    human_takeover_timeout_seconds: int = 180
```

Ao final de `apps/worker/app/clients/agents.py`:

```python
async def sync_context_to_agents(
    http: httpx.AsyncClient,
    *,
    tenant_id: str,
    contact_phone_number: str,
    role: str,
    content: str,
) -> None:
    """POST /conversations/{thread_id}/context — anexa mensagem do takeover ao
    checkpoint do LangGraph (sem LLM, sem débito de créditos)."""
    headers = {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}
    thread_id = f"{tenant_id}:{contact_phone_number}"
    response = await http.post(
        f"/conversations/{thread_id}/context",
        json={"messages": [{"role": role, "content": content}]},
        headers=headers,
    )
    response.raise_for_status()
```

- [ ] **Step 4: Task de mensagens**

Em `apps/worker/app/tasks/messages.py`:

1. Imports: adicionar `sync_context_to_agents` ao import de `app.clients.agents`; garantir `from datetime import UTC, datetime` (já existe).

2. `InboundContext`: adicionar como ÚLTIMO campo (com default, não desloca os existentes):

```python
    human_last_seen_at: datetime | None = None
```

3. `_load_context`: incluir `tables.conversations.c.human_last_seen_at` no select da conversa e repassar `human_last_seen_at=conversation.human_last_seen_at` na construção do `InboundContext`.

4. Helpers novos (antes de `process_inbound_message`):

```python
def _takeover_expirado(human_last_seen_at: datetime | None) -> bool:
    """Sem heartbeat recente do painel, a presença expirou (NULL = expirado)."""
    if human_last_seen_at is None:
        return True
    idade = (datetime.now(UTC) - human_last_seen_at).total_seconds()
    return idade > settings.human_takeover_timeout_seconds


async def _sync_context(
    http: httpx.AsyncClient, tenant_id: str, contact_phone_number: str, content: str
) -> None:
    """Best-effort: falha no sync não pode quebrar o processamento."""
    try:
        await sync_context_to_agents(
            http,
            tenant_id=tenant_id,
            contact_phone_number=contact_phone_number,
            role="contact",
            content=content,
        )
    except Exception as exc:
        logger.warning(
            "Falha ao sincronizar contexto do takeover | tenant=%s erro=%s", tenant_id, exc
        )
```

5. Substituir o branch de modo humano:

```python
    if inbound.conversation_state != "agent":
        if not _takeover_expirado(inbound.human_last_seen_at):
            # Takeover ativo: a mensagem aparece no painel e entra no
            # checkpoint do agente (memória do takeover) — mas a IA não responde.
            logger.info(
                "Conversa em modo humano, agente não acionado | tenant=%s conversation=%s",
                tenant_id,
                conversation_id,
            )
            await _sync_context(
                http, tenant_id, inbound.contact_phone_number, inbound.message_content
            )
            return
        # Presença do atendente expirou: a IA reassume nesta mesma execução.
        logger.info(
            "Takeover expirado, IA reassume | tenant=%s conversation=%s",
            tenant_id,
            conversation_id,
        )
        async with open_tenant_session(session_factory, tenant_id) as session:
            await session.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == uuid.UUID(conversation_id))
                .values(state="agent", human_last_seen_at=None)
            )
            await session.commit()
```

(O fluxo então CONTINUA para o check de saldo e a chamada ao agente — não retorna.)

6. No branch de saldo esgotado, antes do `return`:

```python
        await _sync_context(
            http, tenant_id, inbound.contact_phone_number, inbound.message_content
        )
```

- [ ] **Step 5: Rodar e ver passar + suíte e lint**

Run: `cd apps/worker && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS (todos, incluindo os pré-existentes) e lint limpo (ignorar o drift pré-existente de `app/worker.py` no format, se ainda existir).

- [ ] **Step 6: Commit**

```bash
git add apps/worker/app/config.py apps/worker/app/clients/agents.py apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_process_inbound_message.py
git commit -m "feat(worker): timeout de presença do takeover + sync de contexto nos silêncios"
```

---

### Task 5: `web` — composer sempre ativo, auto-takeover, popup e heartbeat + CLAUDE.md

**Files:**
- Modify: `apps/web/src/components/ConversationThread.tsx`
- Modify: `CLAUDE.md`
- Test: `apps/web/__tests__/ConversationThread.test.tsx`

**Interfaces:**
- Consumes: `PATCH /conversations/{id}` (existe), `POST /conversations/{id}/heartbeat` (Task 2).
- Produces: nada consumido por outras tasks.

⚠️ Não alterar a lógica de polling de mensagens, o switch existente, o resumo, nem o `MessageBubble` — as mudanças são aditivas e pontuais.

- [ ] **Step 1: Testes (falhando)**

Adicionar ao `describe` de `apps/web/__tests__/ConversationThread.test.tsx` (seguindo os helpers `conversation()`/`jsonResponse()` existentes do arquivo):

```tsx
  it("composer fica habilitado mesmo em modo agent", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={vi.fn()}
        pollMs={0}
      />,
    );

    await waitFor(() =>
      expect(screen.getByLabelText("Resposta")).not.toBeDisabled(),
    );
  });

  it("focar o composer em modo agent assume a conversa e mostra o popup", async () => {
    const onUpdate = vi.fn();
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        return jsonResponse({ ...conversation("human") });
      }
      return jsonResponse([]);
    });

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={onUpdate}
        pollMs={0}
      />,
    );

    await waitFor(() => expect(screen.getByLabelText("Resposta")).not.toBeDisabled());
    fireEvent.focus(screen.getByLabelText("Resposta"));

    await waitFor(() => expect(screen.getByText("IA pausada")).toBeInTheDocument());
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ state: "human" }));
    expect(
      backendFetchMock.mock.calls.some(
        ([path, init]) => path === "conversations/c1" && init?.method === "PATCH",
      ),
    ).toBe(true);
  });

  it("Devolver pra IA faz o PATCH de volta pra agent", async () => {
    const onUpdate = vi.fn();
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        return jsonResponse({ ...conversation(body.state) });
      }
      return jsonResponse([]);
    });

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={onUpdate}
        pollMs={0}
      />,
    );

    await waitFor(() => expect(screen.getByLabelText("Resposta")).not.toBeDisabled());
    fireEvent.focus(screen.getByLabelText("Resposta"));
    await waitFor(() => expect(screen.getByText("IA pausada")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Devolver pra IA" }));

    await waitFor(() =>
      expect(onUpdate).toHaveBeenLastCalledWith(expect.objectContaining({ state: "agent" })),
    );
    expect(screen.queryByText("IA pausada")).not.toBeInTheDocument();
  });

  it("envia heartbeat no ciclo de polling quando em modo human", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("human")}
        onConversationUpdate={vi.fn()}
        pollMs={40}
      />,
    );

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(
          ([path, init]) =>
            path === "conversations/c1/heartbeat" && init?.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("não envia heartbeat em modo agent", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={vi.fn()}
        pollMs={40}
      />,
    );

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(([path]) => String(path).includes("messages")),
      ).toBe(true),
    );
    expect(
      backendFetchMock.mock.calls.some(([path]) => String(path).includes("heartbeat")),
    ).toBe(false);
  });
```

Nota: o teste existente que verifica o composer desabilitado em modo `agent` (se houver) precisa ser ATUALIZADO pro novo comportamento (sempre habilitado) — localizar e ajustar a asserção, nunca deletar o teste.

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/web && pnpm test -- ConversationThread`
Expected: FAIL — composer desabilitado em agent, "IA pausada" inexistente, heartbeat nunca chamado.

- [ ] **Step 3: Implementar no componente**

Em `apps/web/src/components/ConversationThread.tsx`:

1. Estado novo (junto dos existentes):

```tsx
  const [showTakeoverToast, setShowTakeoverToast] = useState(false);
```

2. Heartbeat — efeito novo, após o efeito de polling existente (não tocar no existente):

```tsx
  useEffect(() => {
    if (!pollMs || conversation.state !== "human") {
      return;
    }
    const sendHeartbeat = () =>
      void backendFetch(`conversations/${conversation.id}/heartbeat`, {
        method: "POST",
      }).catch(() => {
        // presença é best-effort; tenta no próximo ciclo
      });
    sendHeartbeat();
    const interval = setInterval(sendHeartbeat, pollMs);
    return () => clearInterval(interval);
  }, [conversation.id, conversation.state, pollMs]);
```

3. Handler de foco (após `toggleState`):

```tsx
  const handleComposerFocus = async () => {
    if (isManual) {
      return;
    }
    setError(null);
    const response = await backendFetch(`conversations/${conversation.id}`, {
      method: "PATCH",
      body: JSON.stringify({ state: "human" }),
    });
    if (response.ok) {
      onConversationUpdate(await response.json());
      setShowTakeoverToast(true);
    } else {
      setError("Não foi possível assumir a conversa. Tente novamente.");
    }
  };
```

4. Composer: no `<input>`, trocar `disabled={!isManual || sending}` por `disabled={sending}`, trocar `placeholder={isManual ? "Escreva sua resposta…" : ""}` por `placeholder="Escreva sua resposta…"` e adicionar `onFocus={() => void handleComposerFocus()}`. O botão de enviar fica COMO ESTÁ (`disabled={!isManual || sending || !draft.trim()}` — enviar continua exigindo modo human; o foco já terá assumido a conversa antes do clique). No hint do rodapé, substituir:

```tsx
        {!isManual ? (
          <p className="mt-2 text-xs text-muted">Assuma a conversa para responder.</p>
        ) : null}
```

por:

```tsx
        {!isManual ? (
          <p className="mt-2 text-xs text-muted">
            Começar a digitar pausa a IA e você assume a conversa.
          </p>
        ) : null}
```

5. Popup lateral — renderizar logo após o `<header>` (irmão dele, dentro do div raiz):

```tsx
      {showTakeoverToast ? (
        <div
          role="status"
          className="fixed right-6 top-20 z-50 w-72 rounded border border-brass bg-surface p-4 shadow-lg"
        >
          <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-brass">
            IA pausada
          </p>
          <p className="mt-1 text-sm leading-relaxed text-ink">
            Você assumiu esta conversa. A IA reassume após 3 minutos sem atividade.
          </p>
          <div className="mt-3 flex items-center gap-4">
            <button
              type="button"
              onClick={() => {
                setShowTakeoverToast(false);
                void toggleState();
              }}
              className="rounded-sm border border-line px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:border-accent hover:text-accent"
            >
              Devolver pra IA
            </button>
            <button
              type="button"
              onClick={() => setShowTakeoverToast(false)}
              className="text-xs text-muted transition-colors hover:text-ink"
            >
              Fechar
            </button>
          </div>
        </div>
      ) : null}
```

Atenção: quando "Devolver pra IA" é clicado, `isManual` ainda é `true` (estado atual), então `toggleState()` faz o PATCH pra `agent` — correto. Não alterar `toggleState`.

- [ ] **Step 4: Rodar e ver passar + suíte e lint**

Run: `cd apps/web && pnpm test && pnpm lint`
Expected: PASS (suíte completa) e lint sem erros novos (2 warnings de `<img>` pré-existentes são aceitos).

- [ ] **Step 5: Atualizar CLAUDE.md**

Três edições verbatim:

1. Localizar (seção Painel de Conversas):

```
    - A definir: como/quando a conversa retorna para o agente (ação manual de "devolver pro agente"? timeout?).
```

Substituir por:

```
    - Retorno pro agente: manual (botão "Devolver ao agente"/switch) ou automático por timeout de presença — o painel envia heartbeat (`POST /conversations/{id}/heartbeat` → `human_last_seen_at`) enquanto a thread está aberta em modo `human`; o worker reverte pra `agent` (lazy, na chegada da próxima mensagem do contato) quando o heartbeat parou há mais de `HUMAN_TAKEOVER_TIMEOUT_SECONDS` (default 180s). Focar o composer em modo `agent` assume a conversa automaticamente (popup lateral "IA pausada" com "Devolver pra IA"). Mensagens do takeover (atendente e contato) são sincronizadas no checkpoint do LangGraph via `POST /conversations/{thread_id}/context` do `agents` (best-effort, sem LLM/débito) — a IA reassume sabendo o que foi conversado.
```

2. Localizar (Agents Service, lista de endpoints):

```
- Endpoints: `POST /` (webhook), `GET /agents` (lista agentes/tools disponíveis, para dashboards), `DELETE /conversations/{thread_id}` (apaga histórico de uma conversa), `POST /summaries` (resumo de conversa sob demanda, chamada direta ao LLM sem grafo — usado pelo `api` na feature de resumo do painel de conversas).
```

Substituir por:

```
- Endpoints: `POST /` (webhook), `GET /agents` (lista agentes/tools disponíveis, para dashboards), `DELETE /conversations/{thread_id}` (apaga histórico de uma conversa), `POST /conversations/{thread_id}/context` (anexa mensagens do takeover humano ao checkpoint via `aupdate_state` — sem LLM, sem débito; chamado por `api`/`worker`), `POST /summaries` (resumo de conversa sob demanda, chamada direta ao LLM sem grafo — usado pelo `api` na feature de resumo do painel de conversas).
```

3. Localizar (Pendências):

```
- [ ] Mecânica de retorno da conversa de `human` para `agent` (ação manual? timeout?).
```

Substituir por:

```
- [x] ~~Mecânica de retorno da conversa de `human` para `agent`~~ (feito — heartbeat de presença + reversão lazy no worker por `HUMAN_TAKEOVER_TIMEOUT_SECONDS`, auto-takeover ao focar o composer, e contexto do takeover sincronizado no checkpoint; ver Painel de Conversas).
```

Se algum trecho não for encontrado verbatim, PARAR e reportar (não improvisar).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/ConversationThread.tsx apps/web/__tests__/ConversationThread.test.tsx CLAUDE.md
git commit -m "feat(web): auto-takeover no composer, popup de IA pausada e heartbeat de presença"
```

---

## Nota pós-deploy (manual, fora do código)

- Rodar a migration `0010` acontece automaticamente no deploy (step do Alembic).
- `HUMAN_TAKEOVER_TIMEOUT_SECONDS` é opcional no `.env` (default 180s no worker).
