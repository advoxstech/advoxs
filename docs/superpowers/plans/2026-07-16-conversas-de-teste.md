# Conversas de teste (aba de testes do tenant) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aba "Testes" em `/conversas` onde o tenant conversa com os próprios agentes sem WhatsApp, com conversas persistidas e débito de créditos normal.

**Architecture:** Coluna `conversations.is_test` (migration 0011) + contato sintético `teste-{hex12}`. Rotas novas num arquivo dedicado (`test_conversations.py`): criação, envio síncrono (reusa `send_playground_message` do client, `send_to_whatsapp=false`) com débito igual ao worker, e DELETE test-only com limpeza de checkpoint best-effort. `GET /conversations` ganha `?origin=real|test` (default `real`). No front, abas no `ConversationsPanel` e um `TestConversationThread` dedicado — o `ConversationThread` real não é tocado.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (api), Next.js 15 + Vitest (web).

**Spec:** `docs/superpowers/specs/2026-07-16-conversas-de-teste-design.md`

## Global Constraints

- Contato sintético: `f"teste-{uuid.uuid4().hex[:12]}"`. Migration é a **0011** (`down_revision = "0010"`).
- Débito: mesma fórmula do worker — `math.ceil(tokens / settings.credit_tokens_per_credit)`, `tokens_used`/`credits_consumed` na PRIMEIRA resposta, `credit_transactions` tipo `consumption` com `related_message_id` = primeira resposta, saldo atualizado na MESMA transação.
- A mensagem do contato é commitada ANTES da chamada ao agents (falha do agents → 502, mas a mensagem sobrevive).
- `grouped` (202/debounce, `result is None`): nada de resposta persistida, nada debitado.
- DELETE: só `is_test=true` (409 pra real); `credit_transactions.related_message_id` → NULL antes de apagar as mensagens; cleanup do checkpoint via `delete_playground_conversation(f"{tenant_id}:{contact}")` (já é best-effort internamente).
- `GET /conversations?origin=` default `real` — comportamento atual preservado sem query param.
- Respostas de teste têm `delivery_status=NULL` (não houve envio) e `state` da conversa fica `agent` sempre.
- Textos do front: aba "Testes", botão "Nova conversa de teste", rótulo "Conversa de teste", bolha do usuário "Você (cliente)", indicador "digitando…", 402 → "Saldo de créditos esgotado" com link pra `/creditos`.
- Comandos: api → `cd apps/api && uv run pytest tests/unit -q` + `uv run ruff check . && uv run ruff format --check .`; worker → idem em `apps/worker`; web → `cd apps/web && pnpm test` + `pnpm lint`.

---

### Task 1: `is_test` no schema + filtro `origin` na listagem

**Files:**
- Create: `apps/api/alembic/versions/0011_conversation_is_test.py`
- Modify: `apps/api/app/models/conversation.py` (coluna), `apps/api/app/schemas/conversations.py` (`ConversationOut.is_test`), `apps/api/app/api/v1/conversations.py` (query param `origin`), `apps/worker/app/tables.py` (espelho da coluna)
- Test: `apps/api/tests/unit/test_conversations_routes.py`

**Interfaces:**
- Produces: coluna `conversations.is_test` (bool NOT NULL default false); `ConversationOut.is_test: bool`; `GET /api/v1/conversations?origin=real|test`. Tasks 2-4 dependem.

- [ ] **Step 1: Testes (falhando)**

Em `apps/api/tests/unit/test_conversations_routes.py`: primeiro, adicionar `is_test=False` ao factory de conversa existente do arquivo (o `SimpleNamespace`/helper usado pelos testes — sem isso o `ConversationOut.model_validate` falha em TODOS os testes existentes após o schema mudar). Depois adicionar:

```python
class TestOriginFilter:
    def test_default_exclui_conversas_de_teste(self, client, session) -> None:
        session.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )

        response = client.get("/api/v1/conversations")

        assert response.status_code == 200
        # o filtro is_test == False entrou na query
        where_clause = str(session.execute.await_args.args[0])
        assert "is_test" in where_clause

    def test_origin_test_filtra_conversas_de_teste(self, client, session) -> None:
        session.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )

        response = client.get("/api/v1/conversations?origin=test")

        assert response.status_code == 200
        assert "is_test" in str(session.execute.await_args.args[0])

    def test_origin_invalido_retorna_422(self, client) -> None:
        response = client.get("/api/v1/conversations?origin=banana")
        assert response.status_code == 422
```

(Adaptar aos mocks reais do arquivo: se a listagem já tem teste com session.execute mockado, seguir aquele desenho.)

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py -q`
Expected: FAIL — 422 no `origin` (param não existe ainda aceita qualquer coisa? Não: sem o param, `?origin=banana` é ignorado e retorna 200 — o teste de 422 falha; e `is_test` ausente da query).

- [ ] **Step 3: Migration + modelo + schema + espelho**

`apps/api/alembic/versions/0011_conversation_is_test.py`:

```python
"""is_test em conversations

Conversas de teste (aba Testes do painel): o tenant conversa com os próprios
agentes sem WhatsApp, com contato sintético teste-{hex12}. Default false —
nenhuma conversa existente vira teste.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-16
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("conversations", "is_test")
```

`apps/api/app/models/conversation.py` — adicionar `Boolean` ao import de `sqlalchemy` e, após `state`:

```python
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
```

`apps/api/app/schemas/conversations.py` — em `ConversationOut`, após `state`:

```python
    is_test: bool
```

`apps/worker/app/tables.py` — na tabela `conversations`, adicionar (import `Boolean` se ausente):

```python
    Column("is_test", Boolean, nullable=False),
```

- [ ] **Step 4: Filtro na listagem**

Em `apps/api/app/api/v1/conversations.py`, adicionar `from typing import Literal` (se ausente) e mudar `list_conversations`:

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

- [ ] **Step 5: Rodar e ver passar + suítes/lint (api E worker)**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Run: `cd apps/worker && uv run pytest tests/unit -q && uv run ruff check .`
Expected: PASS nos dois; lint limpo.

- [ ] **Step 6: Commit**

```bash
git add apps/api/alembic/versions/0011_conversation_is_test.py apps/api/app/models/conversation.py apps/api/app/schemas/conversations.py apps/api/app/api/v1/conversations.py apps/worker/app/tables.py apps/api/tests/unit/test_conversations_routes.py
git commit -m "feat(api): coluna is_test em conversations + filtro origin na listagem (migration 0011)"
```

---

### Task 2: criação e envio de mensagem de teste

**Files:**
- Create: `apps/api/app/api/v1/test_conversations.py`, `apps/api/app/services/test_conversations.py`, `apps/api/tests/unit/test_test_conversations_routes.py`
- Modify: `apps/api/app/schemas/conversations.py` (schema `TestMessagesOut`), `apps/api/app/api/v1/router.py` (registrar router)

**Interfaces:**
- Consumes: `send_playground_message(*, tenant_id: str, contact_phone_number: str, message: str) -> dict | None` (existe em `app/clients/agents.py`; `None` = 202/debounce); `ConversationOut.is_test` (Task 1); `settings.credit_tokens_per_credit` (existe).
- Produces: `POST /api/v1/test-conversations` → 201 `ConversationOut`; `POST /api/v1/conversations/{id}/test-messages` body `{content}` → 201 `TestMessagesOut{messages: list[MessageOut], grouped: bool}`. Task 4 consome.

- [ ] **Step 1: Testes (falhando)**

Criar `apps/api/tests/unit/test_test_conversations_routes.py` (fixtures no mesmo desenho de `test_conversations_routes.py` — session `AsyncMock` com `add = MagicMock()`, overrides de `get_current_tenant`/`get_tenant_session`; estudar o arquivo vizinho antes):

```python
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.test_conversations as test_conversations_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.agents import AgentsNetworkError
from app.main import app

TENANT_ID = uuid.uuid4()
CONVERSATION_ID = uuid.uuid4()


def _conversation(is_test: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        contact_phone_number="teste-abc123def456",
        state="agent",
        is_test=is_test,
        last_message_at=None,
        created_at=__import__("datetime").datetime(2026, 7, 16, tzinfo=__import__("datetime").UTC),
        summary=None,
        summary_generated_at=None,
    )


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


class TestCreate:
    def test_cria_conversa_de_teste(self, client, session) -> None:
        async def fake_refresh(obj):
            obj.id = CONVERSATION_ID
            obj.state = "agent"
            obj.created_at = _conversation().created_at
            obj.last_message_at = None
            obj.summary = None
            obj.summary_generated_at = None

        session.refresh.side_effect = fake_refresh

        response = client.post("/api/v1/test-conversations")

        assert response.status_code == 201
        body = response.json()
        assert body["is_test"] is True
        assert body["contact_phone_number"].startswith("teste-")
        session.add.assert_called_once()
        session.commit.assert_awaited()


class TestSendTestMessage:
    @pytest.fixture
    def playground_mock(self, monkeypatch):
        mock = AsyncMock(
            return_value={
                "responses": ["resposta 1", "resposta 2"],
                "tokens_used": 3500,
                "current_agent": "agente_secretaria",
            }
        )
        monkeypatch.setattr(
            test_conversations_module.service, "send_playground_message", mock
        )
        return mock

    def _arm_session(self, session, conversation, balance=1000):
        # scalar: 1ª chamada resolve a conversa; get: tenant com saldo
        session.scalar.return_value = conversation
        session.get.return_value = SimpleNamespace(id=TENANT_ID, credit_balance=balance)

        async def fake_refresh(obj):
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if getattr(obj, "created_at", None) is None:
                obj.created_at = conversation.created_at
            for campo in ("media_url", "media_type", "delivery_status"):
                if not hasattr(obj, campo):
                    setattr(obj, campo, None)

        session.refresh.side_effect = fake_refresh

    def test_fluxo_feliz_persiste_e_debita(self, client, session, playground_mock) -> None:
        self._arm_session(session, _conversation())

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            json={"content": "olá, quero saber sobre condomínio"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["grouped"] is False
        assert len(body["messages"]) == 3  # contato + 2 respostas
        assert body["messages"][0]["sender_type"] == "contact"
        assert body["messages"][1]["sender_type"] == "agent"
        playground_mock.assert_awaited_once()
        assert (
            playground_mock.await_args.kwargs["contact_phone_number"]
            == "teste-abc123def456"
        )

    def test_conversa_real_retorna_409(self, client, session, playground_mock) -> None:
        self._arm_session(session, _conversation(is_test=False))

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            json={"content": "oi"},
        )

        assert response.status_code == 409
        playground_mock.assert_not_awaited()

    def test_sem_saldo_retorna_402(self, client, session, playground_mock) -> None:
        self._arm_session(session, _conversation(), balance=0)

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            json={"content": "oi"},
        )

        assert response.status_code == 402
        playground_mock.assert_not_awaited()

    def test_grouped_nao_persiste_resposta(self, client, session, playground_mock) -> None:
        playground_mock.return_value = None
        self._arm_session(session, _conversation())

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            json={"content": "oi"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["grouped"] is True
        assert len(body["messages"]) == 1  # só a do contato

    def test_falha_do_agents_retorna_502(self, client, session, playground_mock) -> None:
        playground_mock.side_effect = AgentsNetworkError("fora do ar")
        self._arm_session(session, _conversation())

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            json={"content": "oi"},
        )

        assert response.status_code == 502
        # a mensagem do contato foi commitada antes da chamada
        session.commit.assert_awaited()

    def test_conversa_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            json={"content": "oi"},
        )

        assert response.status_code == 404
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_test_conversations_routes.py -q`
Expected: FAIL — `ModuleNotFoundError: app.api.v1.test_conversations`.

- [ ] **Step 3: Schema + service**

Em `apps/api/app/schemas/conversations.py`, ao final:

```python
class TestMessagesOut(BaseModel):
    messages: list[MessageOut]
    grouped: bool
```

Criar `apps/api/app/services/test_conversations.py`:

```python
"""Conversas de teste: o tenant conversa com os próprios agentes sem WhatsApp.

Diferente do playground do admin (efêmero), aqui tudo persiste em
conversations/messages e o consumo debita créditos normalmente — teste gasta
token real de LLM.
"""

import math
import uuid
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.agents import send_playground_message
from app.core.config import settings
from app.models import Conversation, CreditTransaction, Message, Tenant


async def send_test_message(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    conversation: Conversation,
    content: str,
) -> tuple[list[Message], bool]:
    """Persiste a mensagem do usuário (como contato), roda o agente síncrono e
    persiste/debita as respostas. Retorna (mensagens novas, grouped)."""
    now = datetime.now(UTC)
    contact_message = Message(
        conversation_id=conversation.id,
        tenant_id=tenant_id,
        sender_type="contact",
        content=content,
        created_at=now,
    )
    session.add(contact_message)
    conversation.last_message_at = now
    # Commit ANTES da chamada ao agents: se ele falhar, a mensagem do usuário
    # sobrevive no histórico (mesma filosofia do fluxo real via webhook).
    await session.commit()
    await session.refresh(contact_message)

    result = await send_playground_message(
        tenant_id=str(tenant_id),
        contact_phone_number=conversation.contact_phone_number,
        message=content,
    )
    if result is None:
        # 202: debounce agrupou numa execução em andamento — as respostas
        # serão persistidas pela requisição que está rodando.
        return [contact_message], True

    responses: list[str] = result["responses"]
    tokens_used = result["tokens_used"] or 0
    credits = math.ceil(tokens_used / settings.credit_tokens_per_credit) if tokens_used else 0

    now = datetime.now(UTC)
    agent_messages: list[Message] = []
    for i, text in enumerate(responses):
        message = Message(
            conversation_id=conversation.id,
            tenant_id=tenant_id,
            sender_type="agent",
            content=text,
            created_at=now,
            tokens_used=tokens_used if i == 0 else None,
            credits_consumed=credits if i == 0 else None,
        )
        session.add(message)
        agent_messages.append(message)
    conversation.last_message_at = now
    await session.flush()

    if credits and agent_messages:
        # Ledger + saldo na mesma transação das mensagens (fórmula do worker).
        session.add(
            CreditTransaction(
                tenant_id=tenant_id,
                type="consumption",
                amount_credits=-credits,
                related_message_id=agent_messages[0].id,
                description=f"Consumo do agente em conversa de teste ({tokens_used} tokens)",
            )
        )
        await session.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(credit_balance=Tenant.credit_balance - credits)
        )
    await session.commit()
    for message in agent_messages:
        await session.refresh(message)
    return [contact_message, *agent_messages], False
```

- [ ] **Step 4: Rotas**

Criar `apps/api/app/api/v1/test_conversations.py`:

```python
"""Conversas de teste — aba Testes do painel do tenant (sem WhatsApp)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.agents import AgentsApiError, AgentsNetworkError
from app.models import Conversation, Tenant
from app.schemas.conversations import (
    ConversationOut,
    MessageOut,
    SendMessageRequest,
    TestMessagesOut,
)
from app.services import test_conversations as service

router = APIRouter(tags=["test-conversations"])

_AGENTS_ERROR_DETAIL = "Não foi possível falar com o agente agora — tente novamente"


@router.post("/test-conversations", status_code=status.HTTP_201_CREATED)
async def create_test_conversation(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ConversationOut:
    conversation = Conversation(
        tenant_id=ctx.tenant_id,
        contact_phone_number=f"teste-{uuid.uuid4().hex[:12]}",
        is_test=True,
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.post(
    "/conversations/{conversation_id}/test-messages",
    status_code=status.HTTP_201_CREATED,
)
async def send_test_message(
    conversation_id: uuid.UUID,
    body: SendMessageRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> TestMessagesOut:
    conversation = await _get_test_conversation(conversation_id, ctx, session)

    tenant = await session.get(Tenant, ctx.tenant_id)
    if tenant.credit_balance <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Saldo de créditos esgotado — não é possível testar o agente",
        )

    try:
        messages, grouped = await service.send_test_message(
            session, ctx.tenant_id, conversation, body.content
        )
    except (AgentsNetworkError, AgentsApiError):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_AGENTS_ERROR_DETAIL)

    return TestMessagesOut(
        messages=[MessageOut.model_validate(m) for m in messages], grouped=grouped
    )


async def _get_test_conversation(
    conversation_id: uuid.UUID, ctx: TenantContext, session: AsyncSession
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == ctx.tenant_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversa não encontrada")
    if not conversation.is_test:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Operação disponível apenas para conversas de teste",
        )
    return conversation
```

Registrar em `apps/api/app/api/v1/router.py` (seguir o padrão dos includes existentes):

```python
from app.api.v1 import test_conversations
# ...
api_router.include_router(test_conversations.router)
```

(Conferir o nome real da variável do router agregador no arquivo — seguir o padrão.)

- [ ] **Step 5: Rodar e ver passar + suíte e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS e lint limpo.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/api/v1/test_conversations.py apps/api/app/services/test_conversations.py apps/api/app/schemas/conversations.py apps/api/app/api/v1/router.py apps/api/tests/unit/test_test_conversations_routes.py
git commit -m "feat(api): criação e envio síncrono de mensagens em conversas de teste"
```

---

### Task 3: DELETE de conversa de teste

**Files:**
- Modify: `apps/api/app/api/v1/test_conversations.py` (rota nova), `apps/api/app/services/test_conversations.py` (função nova)
- Test: `apps/api/tests/unit/test_test_conversations_routes.py`

**Interfaces:**
- Consumes: `delete_playground_conversation(thread_id: str) -> None` (existe em `app/clients/agents.py`, já best-effort com log interno); `_get_test_conversation` (Task 2).
- Produces: `DELETE /api/v1/conversations/{id}` → 204 (409 pra conversa real). Task 4 consome.

- [ ] **Step 1: Testes (falhando)**

Adicionar em `apps/api/tests/unit/test_test_conversations_routes.py`:

```python
class TestDelete:
    def test_apaga_conversa_de_teste(self, client, session, monkeypatch) -> None:
        cleanup_mock = AsyncMock()
        monkeypatch.setattr(
            test_conversations_module.service, "delete_playground_conversation", cleanup_mock
        )
        session.scalar.return_value = _conversation()

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 204
        session.delete.assert_awaited_once()
        session.commit.assert_awaited()
        cleanup_mock.assert_awaited_once_with(f"{TENANT_ID}:teste-abc123def456")

    def test_conversa_real_retorna_409(self, client, session, monkeypatch) -> None:
        cleanup_mock = AsyncMock()
        monkeypatch.setattr(
            test_conversations_module.service, "delete_playground_conversation", cleanup_mock
        )
        session.scalar.return_value = _conversation(is_test=False)

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 409
        session.delete.assert_not_awaited()
        cleanup_mock.assert_not_awaited()

    def test_desvincula_ledger_antes_de_apagar(self, client, session, monkeypatch) -> None:
        monkeypatch.setattr(
            test_conversations_module.service,
            "delete_playground_conversation",
            AsyncMock(),
        )
        session.scalar.return_value = _conversation()

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 204
        # dois executes: UPDATE credit_transactions (related_message_id=NULL)
        # e DELETE messages, nessa ordem
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        update_idx = next(i for i, s in enumerate(statements) if "credit_transactions" in s)
        delete_idx = next(i for i, s in enumerate(statements) if "DELETE FROM messages" in s)
        assert update_idx < delete_idx
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_test_conversations_routes.py::TestDelete -q`
Expected: FAIL — 405 (rota DELETE não existe).

- [ ] **Step 3: Service + rota**

Em `apps/api/app/services/test_conversations.py`, adicionar aos imports `delete as sql_delete`, `select` de `sqlalchemy` e `delete_playground_conversation` do client, e ao final:

```python
async def delete_test_conversation(
    session: AsyncSession, tenant_id: uuid.UUID, conversation: Conversation
) -> None:
    """Apaga mensagens + conversa; ledger fica (related_message_id vira NULL,
    o consumo continua auditável). Checkpoint no agents é limpado best-effort."""
    thread_id = f"{tenant_id}:{conversation.contact_phone_number}"

    message_ids = select(Message.id).where(Message.conversation_id == conversation.id)
    await session.execute(
        update(CreditTransaction)
        .where(CreditTransaction.related_message_id.in_(message_ids))
        .values(related_message_id=None)
    )
    await session.execute(sql_delete(Message).where(Message.conversation_id == conversation.id))
    await session.delete(conversation)
    await session.commit()

    # Best-effort (a função do client já loga e engole falhas internamente).
    await delete_playground_conversation(thread_id)
```

Em `apps/api/app/api/v1/test_conversations.py`, adicionar a rota:

```python
@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_test_conversation(
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    conversation = await _get_test_conversation(conversation_id, ctx, session)
    await service.delete_test_conversation(session, ctx.tenant_id, conversation)
```

- [ ] **Step 4: Rodar e ver passar + suíte e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS e lint limpo.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/api/v1/test_conversations.py apps/api/app/services/test_conversations.py apps/api/tests/unit/test_test_conversations_routes.py
git commit -m "feat(api): DELETE de conversa de teste com limpeza de checkpoint e ledger preservado"
```

---

### Task 4: web — abas, TestConversationThread e CLAUDE.md

**Files:**
- Create: `apps/web/src/components/TestConversationThread.tsx`, `apps/web/__tests__/TestConversationThread.test.tsx`, `apps/web/__tests__/ConversationsPanel.test.tsx`
- Modify: `apps/web/src/lib/types.ts` (`Conversation.is_test`), `apps/web/src/components/ConversationsPanel.tsx` (abas), `apps/web/src/components/ConversationList.tsx` (rótulo de teste), `CLAUDE.md`
- Test: os dois arquivos novos + ajustar mocks existentes que constroem `Conversation` (adicionar `is_test: false`)

**Interfaces:**
- Consumes: `POST test-conversations` (201 → `Conversation`), `POST conversations/{id}/test-messages` (`{messages, grouped}`), `DELETE conversations/{id}` (204), `GET conversations?origin=` (Task 1-3), `GET conversations/{id}/messages` (existe).

⚠️ NÃO tocar em `ConversationThread.tsx` — a thread de teste é um componente novo e independente.

- [ ] **Step 1: Atualizar o type e os mocks existentes**

`apps/web/src/lib/types.ts` — em `Conversation`, após `state`:

```ts
  is_test: boolean;
```

Buscar todos os literais `Conversation` em testes (`grep -rn "state:" apps/web/__tests__/ | grep -l conversation` — na prática, os factories `conversation()` de `ConversationThread.test.tsx` e `ConversationList.test.tsx`) e adicionar `is_test: false`.

Run: `cd apps/web && pnpm test`
Expected: PASS (só type/factories, sem mudança de comportamento).

- [ ] **Step 2: Testes das abas (falhando)**

Criar `apps/web/__tests__/ConversationsPanel.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationsPanel } from "@/components/ConversationsPanel";
import { backendFetch } from "@/lib/client-api";
import type { Conversation } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const backendFetchMock = vi.mocked(backendFetch);

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function conversation(id: string, isTest: boolean): Conversation {
  return {
    id,
    contact_phone_number: isTest ? `teste-${id}` : "5511999998888",
    state: "agent",
    is_test: isTest,
    last_message_at: null,
    created_at: new Date().toISOString(),
    summary: null,
    summary_generated_at: null,
  };
}

beforeEach(() => {
  backendFetchMock.mockReset();
});

describe("ConversationsPanel — abas", () => {
  it("aba padrão busca origin=real e a de testes origin=test", async () => {
    backendFetchMock.mockImplementation(async (path: string) => {
      if (String(path).includes("origin=test")) {
        return jsonResponse([conversation("t1", true)]);
      }
      return jsonResponse([conversation("r1", false)]);
    });

    render(<ConversationsPanel pollMs={0} />);

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(([p]) => String(p).includes("origin=real")),
      ).toBe(true),
    );

    fireEvent.click(screen.getByRole("button", { name: "Testes" }));

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(([p]) => String(p).includes("origin=test")),
      ).toBe(true),
    );
    expect(screen.getByText("Nova conversa de teste")).toBeInTheDocument();
  });

  it("nova conversa de teste cria e seleciona", async () => {
    const created = conversation("novo", true);
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (String(path) === "test-conversations" && init?.method === "POST") {
        return jsonResponse(created, 201);
      }
      return jsonResponse([]);
    });

    render(<ConversationsPanel pollMs={0} />);

    fireEvent.click(screen.getByRole("button", { name: "Testes" }));
    fireEvent.click(await screen.findByText("Nova conversa de teste"));

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(
          ([p, init]) => String(p) === "test-conversations" && init?.method === "POST",
        ),
      ).toBe(true),
    );
  });
});
```

Run: `cd apps/web && pnpm test -- ConversationsPanel`
Expected: FAIL — botão "Testes" não existe.

- [ ] **Step 3: Abas no ConversationsPanel + rótulo na lista**

`apps/web/src/components/ConversationsPanel.tsx` — substituir o componente inteiro por:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type { Conversation } from "@/lib/types";

import { ConversationList } from "./ConversationList";
import { ConversationThread } from "./ConversationThread";
import { TestConversationThread } from "./TestConversationThread";

type Origin = "real" | "test";

export function ConversationsPanel({ pollMs = 5000 }: { pollMs?: number }) {
  const [origin, setOrigin] = useState<Origin>("real");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const loadConversations = useCallback(async () => {
    try {
      const response = await backendFetch(`conversations?origin=${origin}`);
      if (response.ok) {
        setConversations(await response.json());
      }
    } catch {
      // rede indisponível: mantém a lista atual e tenta no próximo ciclo
    } finally {
      setLoaded(true);
    }
  }, [origin]);

  useEffect(() => {
    setLoaded(false);
    void loadConversations();
    if (!pollMs) {
      return;
    }
    const interval = setInterval(() => void loadConversations(), pollMs);
    return () => clearInterval(interval);
  }, [loadConversations, pollMs]);

  const selected = conversations.find((c) => c.id === selectedId) ?? null;

  const handleConversationUpdate = (updated: Conversation) => {
    setConversations((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
  };

  const switchTab = (next: Origin) => {
    if (next === origin) return;
    setOrigin(next);
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
    <div className="flex min-w-0 flex-1">
      <aside className="flex w-80 shrink-0 flex-col border-r border-line">
        <header className="border-b border-line px-5 py-4">
          <div className="flex items-baseline justify-between">
            <h1 className="font-display text-xl font-semibold">Conversas</h1>
            <span className="font-mono text-xs text-muted">{conversations.length}</span>
          </div>
          <div className="mt-3 flex gap-1">
            <button
              type="button"
              onClick={() => switchTab("real")}
              aria-pressed={origin === "real"}
              className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
                origin === "real" ? "bg-ink text-ground" : "text-muted hover:text-ink"
              }`}
            >
              Conversas
            </button>
            <button
              type="button"
              onClick={() => switchTab("test")}
              aria-pressed={origin === "test"}
              className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
                origin === "test" ? "bg-ink text-ground" : "text-muted hover:text-ink"
              }`}
            >
              Testes
            </button>
          </div>
        </header>
        {origin === "test" ? (
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
              {origin === "test"
                ? "Crie uma conversa de teste para experimentar os agentes sem WhatsApp."
                : "Selecione uma conversa para acompanhar o atendimento."}
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
```

`apps/web/src/components/ConversationList.tsx` — no `<span>` do telefone, trocar:

```tsx
                <span className="truncate font-mono text-sm font-medium">
                  {formatPhone(conversation.contact_phone_number)}
                </span>
```

por:

```tsx
                <span className="truncate font-mono text-sm font-medium">
                  {conversation.is_test
                    ? "Conversa de teste"
                    : formatPhone(conversation.contact_phone_number)}
                </span>
```

- [ ] **Step 4: Testes do TestConversationThread (falhando)**

Criar `apps/web/__tests__/TestConversationThread.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TestConversationThread } from "@/components/TestConversationThread";
import { backendFetch } from "@/lib/client-api";
import type { Conversation, Message } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const backendFetchMock = vi.mocked(backendFetch);

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

const conversation: Conversation = {
  id: "t1",
  contact_phone_number: "teste-abc123",
  state: "agent",
  is_test: true,
  last_message_at: null,
  created_at: new Date().toISOString(),
  summary: null,
  summary_generated_at: null,
};

function message(id: string, sender: Message["sender_type"], content: string): Message {
  return {
    id,
    sender_type: sender,
    content,
    media_url: null,
    media_type: null,
    delivery_status: null,
    created_at: new Date().toISOString(),
  };
}

beforeEach(() => {
  backendFetchMock.mockReset();
});

describe("TestConversationThread", () => {
  it("envia mensagem e renderiza a resposta do agente", async () => {
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (String(path).endsWith("/test-messages") && init?.method === "POST") {
        return jsonResponse(
          {
            messages: [
              message("m1", "contact", "olá"),
              message("m2", "agent", "Oi! Como posso ajudar?"),
            ],
            grouped: false,
          },
          201,
        );
      }
      return jsonResponse([]);
    });

    render(<TestConversationThread conversation={conversation} onDeleted={vi.fn()} />);

    const input = await screen.findByLabelText("Mensagem de teste");
    fireEvent.change(input, { target: { value: "olá" } });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));

    await waitFor(() =>
      expect(screen.getByText("Oi! Como posso ajudar?")).toBeInTheDocument(),
    );
    expect(screen.getByText("olá")).toBeInTheDocument();
  });

  it("mostra aviso de saldo esgotado no 402", async () => {
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return jsonResponse({ detail: "Saldo esgotado" }, 402);
      }
      return jsonResponse([]);
    });

    render(<TestConversationThread conversation={conversation} onDeleted={vi.fn()} />);

    const input = await screen.findByLabelText("Mensagem de teste");
    fireEvent.change(input, { target: { value: "olá" } });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));

    await waitFor(() =>
      expect(screen.getByText(/Saldo de créditos esgotado/)).toBeInTheDocument(),
    );
  });

  it("exclui a conversa com confirmação", async () => {
    const onDeleted = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "DELETE") {
        return jsonResponse(null, 204);
      }
      return jsonResponse([]);
    });

    render(<TestConversationThread conversation={conversation} onDeleted={onDeleted} />);

    fireEvent.click(await screen.findByRole("button", { name: "Excluir conversa" }));

    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
  });
});
```

Run: `cd apps/web && pnpm test -- TestConversationThread`
Expected: FAIL — componente não existe.

- [ ] **Step 5: Implementar o TestConversationThread**

Criar `apps/web/src/components/TestConversationThread.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import { formatMessageTime } from "@/lib/format";
import type { Conversation, Message } from "@/lib/types";

interface TestConversationThreadProps {
  conversation: Conversation;
  onDeleted: () => void;
  pollMs?: number;
}

export function TestConversationThread({
  conversation,
  onDeleted,
  pollMs = 4000,
}: TestConversationThreadProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [grouped, setGrouped] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadMessages = useCallback(async () => {
    try {
      const response = await backendFetch(`conversations/${conversation.id}/messages`);
      if (response.ok) {
        const data: Message[] = await response.json();
        setMessages(data.slice().reverse());
      }
    } catch {
      // rede indisponível: tenta no próximo ciclo
    }
  }, [conversation.id]);

  useEffect(() => {
    void loadMessages();
    if (!pollMs) {
      return;
    }
    const interval = setInterval(() => void loadMessages(), pollMs);
    return () => clearInterval(interval);
  }, [loadMessages, pollMs]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "auto", block: "end" });
  }, [messages.length]);

  const sendMessage = async (event: React.FormEvent) => {
    event.preventDefault();
    const content = draft.trim();
    if (!content || sending) {
      return;
    }
    setSending(true);
    setError(null);
    setGrouped(false);
    try {
      const response = await backendFetch(`conversations/${conversation.id}/test-messages`, {
        method: "POST",
        body: JSON.stringify({ content }),
      });
      if (response.ok) {
        const body: { messages: Message[]; grouped: boolean } = await response.json();
        setMessages((prev) => [...prev, ...body.messages]);
        setGrouped(body.grouped);
        setDraft("");
      } else if (response.status === 402) {
        setError("Saldo de créditos esgotado — compre créditos para testar os agentes.");
      } else {
        setError("Não foi possível falar com o agente. Tente novamente.");
        void loadMessages();
      }
    } catch {
      setError("Falha de conexão — tente novamente.");
    } finally {
      setSending(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm("Excluir esta conversa de teste? O histórico será apagado.")) {
      return;
    }
    const response = await backendFetch(`conversations/${conversation.id}`, {
      method: "DELETE",
    });
    if (response.ok) {
      onDeleted();
    } else {
      setError("Não foi possível excluir. Tente novamente.");
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center justify-between gap-4 border-b border-line bg-surface px-6 py-3.5">
        <div className="flex items-center gap-3">
          <h2 className="font-mono text-sm font-medium">Conversa de teste</h2>
          <span className="rounded-full bg-brass-soft px-3 py-1 font-mono text-[10px] uppercase tracking-[0.15em] text-brass">
            ambiente de teste
          </span>
        </div>
        <button
          type="button"
          onClick={() => void handleDelete()}
          className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
        >
          Excluir conversa
        </button>
      </header>

      <ul className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-6 py-5">
        {messages.map((message) => (
          <TestMessageBubble key={message.id} message={message} />
        ))}
        {sending ? (
          <li className="flex items-start">
            <span className="rounded-md border border-line bg-surface px-3.5 py-2.5 text-sm text-muted">
              digitando…
            </span>
          </li>
        ) : null}
        <div ref={bottomRef} aria-hidden />
      </ul>

      <footer className="border-t border-line bg-surface px-6 py-4">
        {error ? (
          <p role="alert" className="mb-2 text-xs text-danger">
            {error}
            {error.startsWith("Saldo") ? (
              <>
                {" "}
                <a href="/creditos" className="underline">
                  Comprar créditos
                </a>
              </>
            ) : null}
          </p>
        ) : null}
        {grouped ? (
          <p className="mb-2 text-xs text-muted">
            Mensagem agrupada com a anterior — a resposta chega em instantes.
          </p>
        ) : null}
        <form onSubmit={sendMessage} className="flex items-end gap-3">
          <input
            type="text"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={sending}
            placeholder="Escreva como se fosse o cliente…"
            aria-label="Mensagem de teste"
            className="flex-1 rounded-sm border border-line bg-ground px-3 py-2.5 text-sm placeholder:text-muted disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={sending || !draft.trim()}
            className="rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink disabled:opacity-50"
          >
            {sending ? "Enviando…" : "Enviar"}
          </button>
        </form>
        <p className="mt-2 text-xs text-muted">
          Você escreve como o cliente; o agente responde de verdade (consome créditos).
        </p>
      </footer>
    </div>
  );
}

function TestMessageBubble({ message }: { message: Message }) {
  const fromContact = message.sender_type === "contact";

  return (
    <li className={`flex flex-col ${fromContact ? "items-end" : "items-start"}`}>
      <div
        className={`max-w-[72%] rounded-md px-3.5 py-2.5 text-sm leading-relaxed ${
          fromContact ? "bg-brass-soft" : "border border-line bg-surface"
        }`}
      >
        <span
          className={`mb-0.5 block font-mono text-[10px] uppercase tracking-[0.14em] ${
            fromContact ? "text-brass" : "text-accent"
          }`}
        >
          {fromContact ? "Você (cliente)" : "Agente"}
        </span>
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
      </div>
      <time className="mt-1 font-mono text-[10px] text-muted">
        {formatMessageTime(message.created_at)}
      </time>
    </li>
  );
}
```

Nota de layout: na conversa de teste, o usuário É o cliente — as bolhas dele ficam à direita (brass) e as do agente à esquerda, invertendo o ponto de vista da thread real de propósito.

- [ ] **Step 6: Rodar e ver passar + suíte e lint**

Run: `cd apps/web && pnpm test && pnpm lint`
Expected: PASS (suíte completa) e lint sem erros novos.

- [ ] **Step 7: CLAUDE.md**

Localizar (bullet do front de `/conversas`, texto atual após a feature de takeover):

```
Mensagens que falharam ao entregar (`delivery_status="failed"`) mostram um badge "Não entregue" na bolha.
```

Substituir por:

```
Mensagens que falharam ao entregar (`delivery_status="failed"`) mostram um badge "Não entregue" na bolha. A página também tem a aba **Testes**: conversas de teste persistidas (`is_test=true`, contato sintético `teste-{hex12}`, migration `0011`) onde o usuário conversa com os próprios agentes sem WhatsApp — `POST /api/v1/test-conversations` cria, `POST /api/v1/conversations/{id}/test-messages` roda o agente síncrono (`send_to_whatsapp=false`, reusa o caminho do playground) **debitando créditos normalmente** (402 quando o saldo esgota), `DELETE /api/v1/conversations/{id}` (só teste) apaga com limpeza do checkpoint best-effort e ledger preservado (`related_message_id` → NULL); `GET /api/v1/conversations?origin=real|test` separa as listas (default `real` — conversas de teste nunca aparecem na aba principal). Thread de teste é um componente dedicado (`TestConversationThread`, sem takeover/heartbeat/resumo).
```

Se o trecho não for encontrado verbatim, PARAR e reportar (não improvisar).

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/components/ConversationsPanel.tsx apps/web/src/components/ConversationList.tsx apps/web/src/components/TestConversationThread.tsx apps/web/__tests__/ConversationsPanel.test.tsx apps/web/__tests__/TestConversationThread.test.tsx apps/web/__tests__/ConversationThread.test.tsx apps/web/__tests__/ConversationList.test.tsx CLAUDE.md
git commit -m "feat(web): aba Testes em /conversas com thread de teste dedicada"
```

---

## Nota pós-deploy (manual, fora do código)

Nada além do fluxo normal — a migration `0011` roda no pipeline. Nenhuma env nova.
