# Resumo de Conversa e Switch de IA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No painel de conversas (`/conversas`): (1) o botão de takeover atual ("Assumir conversa"/"Devolver ao agente") vira um switch visual "IA respondendo"; (2) um resumo de conversa gerado por IA sob demanda, consumindo créditos do escritório.

**Architecture:** Endpoint novo `POST /summaries` no `agents` (chamada direta ao LLM já configurado em `agents/nodes.py`, sem grafo/tools). O `api` ganha uma rota `POST /conversations/{id}/summary` que orquestra: busca as mensagens, chama o `agents` via um client novo, persiste o resumo em `conversations` e debita créditos na mesma transação (mesma fórmula do `worker`). O switch é um reskin puro do botão existente — a chamada `PATCH /conversations/{id}` não muda.

**Tech Stack:** FastAPI + LangChain/OpenAI (agents), FastAPI + SQLAlchemy async (api), Next.js 15 + React (web).

## Global Constraints

- **Esta feature assume que a migration `0005` (Feature A — Perfil do Escritório) já está em `main`.** A migration desta feature é `0006`, `down_revision = "0005"`. Se esta feature for implementada antes da Feature A, ajustar `down_revision` para `"0004"` e renumerar para `0005` antes de rodar `alembic upgrade head`.
- **Resumo é sempre sob demanda** — nunca automático, nunca em background.
- **Bloqueio de saldo**: `tenants.credit_balance <= 0` → `402 Payment Required` na rota de resumo, sem custo de LLM.
- **Débito de créditos**: mesma fórmula do `worker` (`apps/worker/app/tasks/messages.py:100`) — `math.ceil(tokens_used / settings.credit_tokens_per_credit)`, arredondando pra cima; `related_message_id=None` (é uma ação sobre a conversa, não uma resposta a ela).
- **Sem histórico/versionamento de resumo** — cada geração sobrescreve a anterior.
- Mensagens/comentários em pt-BR com acentuação correta.
- Comandos: `apps/agents` → `uv run pytest tests/unit`, `uv run ruff check .`. `apps/api` → `uv run pytest tests/unit`, `uv run ruff check .`, `uv run ruff format --check .`. `apps/web` → `pnpm test`, `pnpm lint`, `pnpm build` (via `npx --yes pnpm@9 <comando>` se `pnpm` não estiver global).

---

### Task 1: `agents` — endpoint `POST /summaries`

**Files:**
- Create: `apps/agents/services/summarize.py`
- Modify: `apps/agents/api/routes.py`
- Test: `apps/agents/tests/unit/test_summarize.py`
- Modify: `apps/agents/tests/unit/test_routes.py`

**Interfaces:**
- Consumes: `model` (`ChatOpenAI`, `agents/nodes.py:13`); `langfuse_handler`/`sum_usage_tokens` (`services/call_agent.py:12` e `:27-38`); `verify_api_key` (`api/routes.py:17-27`).
- Produces: `summarize_conversation(messages: list[dict]) -> tuple[str, int]` em `services/summarize.py` (retorna `(summary, tokens_used)`); rota `POST /summaries` retornando `{"summary": str, "tokens_used": int}`.

- [ ] **Step 1: Escrever o teste do service que falha**

Criar `apps/agents/tests/unit/test_summarize.py`:

```python
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

import services.summarize as summarize_module


@pytest.fixture
def mock_model(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(summarize_module, "model", mock)
    return mock


class TestSummarizeConversation:
    async def test_gera_resumo_e_soma_os_tokens(self, mock_model) -> None:
        mock_model.ainvoke.return_value = AIMessage(
            content="Cliente perguntou sobre condomínio e o especialista respondeu.",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

        summary, tokens_used = await summarize_module.summarize_conversation(
            [
                {"sender_type": "contact", "content": "Oi, preciso de ajuda com o condomínio"},
                {"sender_type": "agent", "content": "Claro, qual é a dúvida?"},
            ]
        )

        assert summary == "Cliente perguntou sobre condomínio e o especialista respondeu."
        assert tokens_used == 15
        mock_model.ainvoke.assert_awaited_once()

    async def test_monta_a_transcricao_com_rotulos_em_portugues(self, mock_model) -> None:
        mock_model.ainvoke.return_value = AIMessage(content="resumo", usage_metadata=None)

        await summarize_module.summarize_conversation(
            [
                {"sender_type": "contact", "content": "Pergunta do cliente"},
                {"sender_type": "human", "content": "Resposta do atendente humano"},
            ]
        )

        transcript = mock_model.ainvoke.call_args.args[0][1].content
        assert "Cliente: Pergunta do cliente" in transcript
        assert "Atendente: Resposta do atendente humano" in transcript

    async def test_sem_usage_metadata_retorna_zero_tokens(self, mock_model) -> None:
        mock_model.ainvoke.return_value = AIMessage(content="resumo", usage_metadata=None)

        _, tokens_used = await summarize_module.summarize_conversation(
            [{"sender_type": "contact", "content": "oi"}]
        )

        assert tokens_used == 0
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/agents && uv run pytest tests/unit/test_summarize.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'services.summarize'`.

- [ ] **Step 3: Implementar o service**

Criar `apps/agents/services/summarize.py`:

```python
"""Resumo de conversa sob demanda — chamada direta ao LLM, sem grafo/tools.

Diferente de `run_agent` (services/call_agent.py), aqui não há checkpoint nem
histórico persistido: o `api` já manda o histórico completo em cada chamada.
"""

from langchain_core.messages import HumanMessage, SystemMessage

from agents.nodes import model
from services.call_agent import langfuse_handler, sum_usage_tokens

SUMMARY_PROMPT = (
    "Resuma esta conversa entre um cliente e o escritório de advocacia em até "
    "3 frases, em português, focando no problema ou pedido do cliente e no "
    "que já foi resolvido."
)

_SENDER_LABELS = {"contact": "Cliente", "agent": "Atendente", "human": "Atendente"}


def _format_transcript(messages: list[dict]) -> str:
    lines = [
        f"{_SENDER_LABELS.get(m['sender_type'], m['sender_type'])}: {m['content']}"
        for m in messages
    ]
    return "\n".join(lines)


async def summarize_conversation(messages: list[dict]) -> tuple[str, int]:
    transcript = _format_transcript(messages)
    response = await model.ainvoke(
        [SystemMessage(content=SUMMARY_PROMPT), HumanMessage(content=transcript)],
        config={"callbacks": [langfuse_handler]},
    )
    tokens_used = sum_usage_tokens([response])
    return response.content, tokens_used
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/agents && uv run pytest tests/unit/test_summarize.py -v`
Expected: PASS (3/3).

- [ ] **Step 5: Escrever os testes da rota que falham**

Em `apps/agents/tests/unit/test_routes.py`, adicionar ao final do arquivo:

```python
def test_resumo_sem_mensagens_retorna_400(client) -> None:
    response = client.post("/summaries", json={"messages": []})
    assert response.status_code == 400


def test_resumo_chama_summarize_conversation_e_retorna_resultado(client, monkeypatch) -> None:
    summarize = AsyncMock(return_value=("Resumo gerado.", 42))
    monkeypatch.setattr(routes, "summarize_conversation", summarize)

    response = client.post(
        "/summaries",
        json={
            "messages": [
                {"sender_type": "contact", "content": "Oi"},
                {"sender_type": "agent", "content": "Olá, como posso ajudar?"},
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == {"summary": "Resumo gerado.", "tokens_used": 42}
    summarize.assert_awaited_once_with(
        [
            {"sender_type": "contact", "content": "Oi"},
            {"sender_type": "agent", "content": "Olá, como posso ajudar?"},
        ]
    )


def test_resumo_erro_interno_retorna_500(client, monkeypatch) -> None:
    monkeypatch.setattr(
        routes, "summarize_conversation", AsyncMock(side_effect=RuntimeError("boom"))
    )

    response = client.post(
        "/summaries", json={"messages": [{"sender_type": "contact", "content": "oi"}]}
    )

    assert response.status_code == 500
```

- [ ] **Step 6: Rodar e ver falhar**

Run: `cd apps/agents && uv run pytest tests/unit/test_routes.py -v`
Expected: FAIL — `AttributeError: module 'api.routes' has no attribute 'summarize_conversation'` (rota `/summaries` ainda não existe, então nem o `monkeypatch.setattr` encontra o nome no módulo).

- [ ] **Step 7: Implementar a rota**

Em `apps/agents/api/routes.py`, adicionar o import (junto dos demais, após a linha 9):

```python
from services.summarize import summarize_conversation
```

Adicionar, após a classe `IncomingMessage` (após a linha 46, antes de `app = FastAPI()`):

```python
class SummaryMessageIn(BaseModel):
    sender_type: str
    content: str


class SummaryRequest(BaseModel):
    messages: list[SummaryMessageIn]
```

Adicionar, ao final do arquivo:

```python
@app.post("/summaries", dependencies=[Depends(verify_api_key)])
async def summarize(body: SummaryRequest):
    if not body.messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sem mensagens para resumir",
        )

    try:
        summary, tokens_used = await summarize_conversation(
            [{"sender_type": m.sender_type, "content": m.content} for m in body.messages]
        )
    except Exception:
        logger.exception("Erro ao gerar resumo")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao gerar resumo.",
        )

    return {"summary": summary, "tokens_used": tokens_used}
```

- [ ] **Step 8: Rodar e ver passar**

Run: `cd apps/agents && uv run pytest tests/unit/test_routes.py -v`
Expected: PASS em todos.

- [ ] **Step 9: Rodar a suíte completa e lint**

Run: `cd apps/agents && uv run pytest tests/unit -q && uv run ruff check .`
Expected: todos PASS, ruff limpo.

- [ ] **Step 10: Commit**

```bash
git add apps/agents/services/summarize.py apps/agents/api/routes.py apps/agents/tests/unit/test_summarize.py apps/agents/tests/unit/test_routes.py
git commit -m "feat(agents): endpoint POST /summaries para resumo de conversa sob demanda"
```

---

### Task 2: `api` — migration, client, rota `POST /conversations/{id}/summary`

**Files:**
- Create: `apps/api/alembic/versions/0006_conversation_summary.py`
- Modify: `apps/api/app/models/conversation.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/clients/agents.py`
- Modify: `apps/api/app/schemas/conversations.py`
- Modify: `apps/api/app/api/v1/conversations.py`
- Modify: `apps/api/tests/unit/test_conversations_routes.py`

**Interfaces:**
- Consumes: `Tenant`/`CreditTransaction` (`app/models`); `AgentsNetworkError`/`AgentsApiError` (`app/clients/agents.py:16-21`); `settings.credit_tokens_per_credit` (novo).
- Produces: `generate_conversation_summary(messages: list[dict]) -> dict` (`{"summary": str, "tokens_used": int}`) em `app.clients.agents`; rota `POST /api/v1/conversations/{id}/summary`; `ConversationOut` ganha `summary`/`summary_generated_at`.

- [ ] **Step 1: Migration**

Criar `apps/api/alembic/versions/0006_conversation_summary.py`:

```python
"""summary e summary_generated_at em conversations

Resumo de conversa sob demanda, gerado via LLM (agents service) e
persistido aqui — sem histórico, cada geração sobrescreve a anterior.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("summary_generated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "summary_generated_at")
    op.drop_column("conversations", "summary")
```

- [ ] **Step 2: Model**

Em `apps/api/app/models/conversation.py`, adicionar o import de `Text` à linha de imports do `sqlalchemy` (linha 4):

```python
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, text
```

Adicionar as duas colunas novas, após `last_message_at` (linha 29):

```python
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str | None] = mapped_column(Text)
    summary_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 3: Config**

Em `apps/api/app/core/config.py`, adicionar, junto das demais envs (após `agents_api_key` na linha 15):

```python
    agents_api_key: str = ""
    # Conversão de consumo: 1 crédito = N tokens (mesma fórmula do worker,
    # arredondamento sempre pra cima). Usado pelo débito de resumo de conversa.
    credit_tokens_per_credit: int = 1000
```

- [ ] **Step 4: Escrever os testes do client que falham**

Criar `apps/api/tests/unit/test_agents_client.py` (não existe ainda para este módulo — se já existir um arquivo de testes do client, adicionar as classes abaixo a ele):

```python
from unittest.mock import AsyncMock

import httpx
import pytest

from app.clients.agents import (
    AgentsApiError,
    AgentsNetworkError,
    generate_conversation_summary,
)


class TestGenerateConversationSummary:
    async def test_retorna_resumo_e_tokens(self, monkeypatch) -> None:
        response = httpx.Response(
            200, json={"summary": "Resumo da conversa.", "tokens_used": 88}
        )
        mock_post = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        result = await generate_conversation_summary(
            [{"sender_type": "contact", "content": "oi"}]
        )

        assert result == {"summary": "Resumo da conversa.", "tokens_used": 88}
        mock_post.assert_awaited_once()
        assert mock_post.call_args.args[0] == "/summaries"
        assert mock_post.call_args.kwargs["json"] == {
            "messages": [{"sender_type": "contact", "content": "oi"}]
        }

    async def test_erro_http_levanta_agents_api_error(self, monkeypatch) -> None:
        response = httpx.Response(500, text="erro interno")
        monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))

        with pytest.raises(AgentsApiError):
            await generate_conversation_summary([{"sender_type": "contact", "content": "oi"}])

    async def test_falha_de_rede_levanta_agents_network_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            httpx.AsyncClient,
            "post",
            AsyncMock(side_effect=httpx.ConnectError("conexão recusada")),
        )

        with pytest.raises(AgentsNetworkError):
            await generate_conversation_summary([{"sender_type": "contact", "content": "oi"}])
```

- [ ] **Step 5: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'generate_conversation_summary'`.

- [ ] **Step 6: Implementar o client**

Em `apps/api/app/clients/agents.py`, adicionar ao final do arquivo:

```python
async def generate_conversation_summary(messages: list[dict]) -> dict:
    """POST /summaries no agents — resumo sob demanda de uma conversa completa.

    Retorna {"summary": str, "tokens_used": int}.
    """
    payload = {"messages": messages}
    try:
        async with httpx.AsyncClient(
            base_url=settings.agents_service_url, timeout=_TIMEOUT_SECONDS
        ) as client:
            response = await client.post("/summaries", json=payload, headers=_auth_headers())
    except httpx.HTTPError as exc:
        raise AgentsNetworkError(f"Falha de rede ao chamar o agents: {exc}") from exc

    if response.is_error:
        logger.warning(
            "agents retornou erro ao gerar resumo | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise AgentsApiError(f"agents HTTP {response.status_code}")

    data = response.json()
    return {"summary": data["summary"], "tokens_used": data.get("tokens_used", 0)}
```

- [ ] **Step 7: Rodar e ver passar**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_client.py -v`
Expected: PASS (3/3).

- [ ] **Step 8: Schema**

Em `apps/api/app/schemas/conversations.py`, atualizar `ConversationOut`:

```python
class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_phone_number: str
    state: Literal["agent", "human"]
    last_message_at: datetime | None
    created_at: datetime
    summary: str | None
    summary_generated_at: datetime | None
```

- [ ] **Step 9: Atualizar o helper `_conversation` dos testes existentes**

Em `apps/api/tests/unit/test_conversations_routes.py`, atualizar a função `_conversation` (linhas 18-26) para aceitar e devolver os campos novos:

```python
def _conversation(
    state: str = "agent", summary: str | None = None, summary_generated_at=None
) -> SimpleNamespace:
    return SimpleNamespace(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        contact_phone_number="5511999998888",
        state=state,
        last_message_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        summary=summary,
        summary_generated_at=summary_generated_at,
    )
```

Rodar a suíte já existente pra confirmar que nada quebrou com essa mudança (os testes atuais não passam `summary`, então usam o default `None` — comportamento idêntico ao de antes, já que `ConversationOut.model_validate` agora só passa a exigir os atributos, que o helper já provê).

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py -v`
Expected: PASS em todos os testes já existentes (nenhum teste teve asserção alterada neste step).

- [ ] **Step 10: Escrever os testes da rota de resumo que falham**

Em `apps/api/tests/unit/test_conversations_routes.py`, adicionar os imports necessários no topo (junto dos já existentes):

```python
from app.clients.agents import AgentsApiError, AgentsNetworkError
```

Adicionar, ao final do arquivo:

```python
class TestGenerateSummary:
    def test_saldo_esgotado_retorna_402(self, client, session, monkeypatch) -> None:
        session.scalar.return_value = _conversation(state="agent")
        session.get = AsyncMock(return_value=SimpleNamespace(credit_balance=0))
        summarize = AsyncMock()
        monkeypatch.setattr(conversations_module, "generate_conversation_summary", summarize)

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/summary")

        assert response.status_code == 402
        summarize.assert_not_awaited()

    def test_conversa_sem_mensagens_retorna_409(self, client, session) -> None:
        session.scalar.return_value = _conversation(state="agent")
        session.get = AsyncMock(return_value=SimpleNamespace(credit_balance=100))
        session.execute.return_value = _execute_returning([])

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/summary")

        assert response.status_code == 409

    def test_gera_resumo_persiste_e_debita_creditos(self, client, session, monkeypatch) -> None:
        conversation = _conversation(state="agent")
        session.scalar.return_value = conversation
        session.get = AsyncMock(return_value=SimpleNamespace(credit_balance=100))
        history = [
            SimpleNamespace(sender_type="contact", content="Oi, preciso de ajuda"),
            SimpleNamespace(sender_type="agent", content="Claro, qual é a dúvida?"),
        ]
        session.execute.return_value = _execute_returning(history)
        summarize = AsyncMock(
            return_value={"summary": "Resumo da conversa.", "tokens_used": 2500}
        )
        monkeypatch.setattr(conversations_module, "generate_conversation_summary", summarize)

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/summary")

        assert response.status_code == 200
        body = response.json()
        assert body["summary"] == "Resumo da conversa."
        assert conversation.summary == "Resumo da conversa."
        assert conversation.summary_generated_at is not None
        summarize.assert_awaited_once_with(
            [
                {"sender_type": "contact", "content": "Oi, preciso de ajuda"},
                {"sender_type": "agent", "content": "Claro, qual é a dúvida?"},
            ]
        )
        added = session.add.call_args.args[0]
        assert added.tenant_id == TENANT_ID
        assert added.type == "consumption"
        assert added.amount_credits == -3  # ceil(2500 / 1000)
        assert added.related_message_id is None
        session.commit.assert_awaited_once()

    def test_erro_no_agents_retorna_502(self, client, session, monkeypatch) -> None:
        session.scalar.return_value = _conversation(state="agent")
        session.get = AsyncMock(return_value=SimpleNamespace(credit_balance=100))
        session.execute.return_value = _execute_returning(
            [SimpleNamespace(sender_type="contact", content="oi")]
        )
        monkeypatch.setattr(
            conversations_module,
            "generate_conversation_summary",
            AsyncMock(side_effect=AgentsApiError("agents HTTP 500")),
        )

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/summary")

        assert response.status_code == 502

    def test_falha_de_rede_no_agents_retorna_502(self, client, session, monkeypatch) -> None:
        session.scalar.return_value = _conversation(state="agent")
        session.get = AsyncMock(return_value=SimpleNamespace(credit_balance=100))
        session.execute.return_value = _execute_returning(
            [SimpleNamespace(sender_type="contact", content="oi")]
        )
        monkeypatch.setattr(
            conversations_module,
            "generate_conversation_summary",
            AsyncMock(side_effect=AgentsNetworkError("timeout")),
        )

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/summary")

        assert response.status_code == 502

    def test_conversa_de_outro_tenant_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/summary")

        assert response.status_code == 404
```

- [ ] **Step 11: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py -v -k Summary`
Expected: FAIL — `404 Not Found` da rota (`POST /conversations/{id}/summary` ainda não existe no `api`).

- [ ] **Step 12: Implementar a rota**

Em `apps/api/app/api/v1/conversations.py`, atualizar os imports do topo:

```python
"""Painel de conversas: listagem, histórico, takeover, resposta humana e resumo sob demanda."""

import math
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.agents import AgentsApiError, AgentsNetworkError, generate_conversation_summary
from app.clients.whatsapp import WhatsAppSendError, send_text_message
from app.core.config import settings
from app.core.crypto import decrypt_access_token
from app.models import Conversation, CreditTransaction, Message, Tenant, WhatsAppNumber
from app.schemas.conversations import (
    ConversationOut,
    ConversationStateUpdate,
    MessageOut,
    SendMessageRequest,
)
```

Adicionar, após a rota `send_message` (antes de `_get_conversation`, ou seja, logo após a linha 123 do arquivo original):

```python
@router.post("/{conversation_id}/summary")
async def generate_summary(
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ConversationOut:
    """Resumo sob demanda via LLM (agents service) — consome créditos do tenant."""
    conversation = await _get_conversation(conversation_id, ctx, session)

    tenant = await session.get(Tenant, ctx.tenant_id)
    if tenant.credit_balance <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Saldo de créditos esgotado — não é possível gerar o resumo",
        )

    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    history = result.scalars().all()
    if not history:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversa sem mensagens — nada para resumir",
        )

    try:
        summary_result = await generate_conversation_summary(
            [{"sender_type": m.sender_type, "content": m.content} for m in history]
        )
    except (AgentsNetworkError, AgentsApiError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    tokens_used = summary_result["tokens_used"]
    credits = math.ceil(tokens_used / settings.credit_tokens_per_credit) if tokens_used else 0

    conversation.summary = summary_result["summary"]
    conversation.summary_generated_at = datetime.now(UTC)

    if credits:
        session.add(
            CreditTransaction(
                tenant_id=ctx.tenant_id,
                type="consumption",
                amount_credits=-credits,
                related_message_id=None,
                description=f"Resumo de conversa gerado ({tokens_used} tokens)",
            )
        )
        await session.execute(
            update(Tenant)
            .where(Tenant.id == ctx.tenant_id)
            .values(credit_balance=Tenant.credit_balance - credits)
        )

    await session.commit()
    return ConversationOut.model_validate(conversation)
```

- [ ] **Step 13: Rodar e ver passar**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py -v -k Summary`
Expected: PASS em todos (6/6).

- [ ] **Step 14: Rodar a suíte completa, migration e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos PASS, ruff limpo.

Run (ajustar credenciais conforme seu Postgres local; assume que a migration `0005` da Feature A já foi aplicada): `DATABASE_URL="postgresql+asyncpg://advoxs:changeme@localhost:5433/advoxs" REDIS_URL="redis://localhost:6379/0" JWT_SECRET="test" uv run alembic upgrade head`
Expected: aplica a `0006` sem erro.

- [ ] **Step 15: Commit**

```bash
git add apps/api/alembic/versions/0006_conversation_summary.py apps/api/app/models/conversation.py apps/api/app/core/config.py apps/api/app/clients/agents.py apps/api/app/schemas/conversations.py apps/api/app/api/v1/conversations.py apps/api/tests/unit/test_agents_client.py apps/api/tests/unit/test_conversations_routes.py
git commit -m "feat(api): rota de resumo de conversa sob demanda com débito de créditos"
```

---

### Task 3: `web` — switch de IA e seção de resumo em `ConversationThread`

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/format.ts`
- Modify: `apps/web/src/components/ConversationThread.tsx`
- Modify: `apps/web/__tests__/ConversationThread.test.tsx`

**Interfaces:**
- Consumes: `backendFetch` (`@/lib/client-api`); `Conversation`/`Message` (`@/lib/types`, ganham `summary`/`summary_generated_at`).
- Produces: `formatFullDateTime(iso: string) -> string` em `@/lib/format`.

- [ ] **Step 1: Tipo `Conversation`**

Em `apps/web/src/lib/types.ts`, atualizar a interface (linhas 3-9):

```ts
export interface Conversation {
  id: string;
  contact_phone_number: string;
  state: ConversationState;
  last_message_at: string | null;
  created_at: string;
  summary: string | null;
  summary_generated_at: string | null;
}
```

- [ ] **Step 2: Helper de data completa**

Em `apps/web/src/lib/format.ts`, adicionar ao final:

```ts
/** Data e hora completas — usado no carimbo "resumo gerado em". */
export function formatFullDateTime(iso: string): string {
  return new Date(iso).toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
```

- [ ] **Step 3: Escrever os testes que falham**

Em `apps/web/__tests__/ConversationThread.test.tsx`, atualizar o helper `conversation` (linhas 22-29) para incluir os campos novos:

```tsx
function conversation(
  state: "agent" | "human",
  summary: string | null = null,
  summaryGeneratedAt: string | null = null,
): Conversation {
  return {
    id: "c1",
    contact_phone_number: "5511999998888",
    state,
    last_message_at: null,
    created_at: new Date().toISOString(),
    summary,
    summary_generated_at: summaryGeneratedAt,
  };
}
```

Substituir o teste "em modo agente, o campo de resposta fica desativado com orientação" (que hoje verifica `getByRole("button", {name: "Assumir conversa"})`) por:

```tsx
  it("em modo agente, o campo de resposta fica desativado e o switch está ligado", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    expect(screen.getByLabelText("Resposta")).toBeDisabled();
    expect(screen.getByText("Assuma a conversa para responder.")).toBeInTheDocument();
    const switchControl = screen.getByRole("switch", { name: "IA respondendo" });
    expect(switchControl).toHaveAttribute("aria-checked", "true");
  });

  it("em modo manual, o switch aparece desligado", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("human")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    const switchControl = screen.getByRole("switch", { name: "IA respondendo" });
    expect(switchControl).toHaveAttribute("aria-checked", "false");
  });
```

Substituir o teste "assumir conversa envia PATCH e propaga a conversa atualizada" (que clica no botão antigo) por:

```tsx
  it("acionar o switch envia PATCH e propaga a conversa atualizada", async () => {
    const updated = conversation("human");
    backendFetchMock.mockImplementation(async (path, init) => {
      if (init?.method === "PATCH") {
        return jsonResponse(updated);
      }
      return jsonResponse([]);
    });
    const onConversationUpdate = vi.fn();

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={onConversationUpdate}
        pollMs={0}
      />,
    );

    fireEvent.click(screen.getByRole("switch", { name: "IA respondendo" }));

    await waitFor(() => {
      expect(onConversationUpdate).toHaveBeenCalledWith(updated);
    });
    expect(backendFetchMock).toHaveBeenCalledWith(
      "conversations/c1",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ state: "human" }) }),
    );
  });
```

Adicionar, ao final do arquivo (dentro do mesmo `describe("ConversationThread", ...)`), os testes da seção de resumo:

```tsx
  it("sem resumo, mostra o estado vazio e o botão 'Resumir conversa'", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse(messages));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    fireEvent.click(screen.getByText("Resumo da conversa"));

    expect(screen.getByText("Nenhum resumo gerado ainda.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resumir conversa" })).toBeInTheDocument();
  });

  it("com resumo existente, começa expandido com o botão 'Atualizar resumo'", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse(messages));

    render(
      <ConversationThread
        conversation={conversation("agent", "Resumo anterior.", new Date().toISOString())}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    expect(screen.getByText("Resumo anterior.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Atualizar resumo" })).toBeInTheDocument();
  });

  it("gera o resumo com sucesso e propaga a conversa atualizada", async () => {
    const updated = conversation("agent", "Resumo novo gerado.", new Date().toISOString());
    backendFetchMock.mockImplementation(async (path, init) => {
      if (init?.method === "POST" && path === "conversations/c1/summary") {
        return jsonResponse(updated);
      }
      return jsonResponse(messages);
    });
    const onConversationUpdate = vi.fn();

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={onConversationUpdate}
        pollMs={0}
      />,
    );

    fireEvent.click(screen.getByText("Resumo da conversa"));
    fireEvent.click(screen.getByRole("button", { name: "Resumir conversa" }));

    await waitFor(() => {
      expect(onConversationUpdate).toHaveBeenCalledWith(updated);
    });
  });

  it("mostra aviso de saldo esgotado (402) com link para /creditos", async () => {
    backendFetchMock.mockImplementation(async (path, init) => {
      if (init?.method === "POST" && path === "conversations/c1/summary") {
        return jsonResponse({ detail: "Saldo esgotado" }, 402);
      }
      return jsonResponse(messages);
    });

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    fireEvent.click(screen.getByText("Resumo da conversa"));
    fireEvent.click(screen.getByRole("button", { name: "Resumir conversa" }));

    await waitFor(() => {
      expect(
        screen.getByText("Saldo de créditos esgotado — não é possível gerar o resumo."),
      ).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: "Comprar créditos" })).toHaveAttribute(
      "href",
      "/creditos",
    );
  });

  it("desabilita o botão de resumo quando a conversa não tem mensagens", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    fireEvent.click(screen.getByText("Resumo da conversa"));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Resumir conversa" })).toBeDisabled();
    });
  });
```

- [ ] **Step 4: Rodar e ver falhar**

Run: `cd apps/web && npx --yes pnpm@9 test -- ConversationThread`
Expected: FAIL — o switch e a seção de resumo não existem ainda no componente.

- [ ] **Step 5: Implementar o switch e a seção de resumo**

Em `apps/web/src/components/ConversationThread.tsx`, trocar o import do topo (linha 6) para incluir o novo helper:

```tsx
import { formatFullDateTime, formatMessageTime, formatPhone } from "@/lib/format";
```

Adicionar os estados novos, após a linha `const isManual = conversation.state === "human";` (linha 26):

```tsx
  const isManual = conversation.state === "human";

  const [summaryExpanded, setSummaryExpanded] = useState(() => Boolean(conversation.summary));
  const [summarizing, setSummarizing] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const generateSummary = async () => {
    setSummarizing(true);
    setSummaryError(null);
    try {
      const response = await backendFetch(`conversations/${conversation.id}/summary`, {
        method: "POST",
      });
      if (response.ok) {
        const updated: Conversation = await response.json();
        onConversationUpdate(updated);
        setSummaryExpanded(true);
      } else if (response.status === 402) {
        setSummaryError("Saldo de créditos esgotado — não é possível gerar o resumo.");
      } else {
        setSummaryError("Não foi possível gerar o resumo. Tente novamente.");
      }
    } finally {
      setSummarizing(false);
    }
  };
```

Trocar o `import type { Conversation, Message }` (linha 7) para importar `Conversation` como valor de tipo já usado no `updated: Conversation` acima — a linha já existe como `import type { Conversation, Message } from "@/lib/types";`, não precisa mudar.

Substituir todo o bloco do `<header>` (linhas 96-123 do arquivo original) por:

```tsx
      <header className="flex items-center justify-between gap-4 border-b border-line bg-surface px-6 py-3.5">
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
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted">IA respondendo</span>
          <button
            type="button"
            role="switch"
            aria-checked={!isManual}
            aria-label="IA respondendo"
            onClick={() => void toggleState()}
            className={`relative h-5 w-9 rounded-full transition-colors ${
              !isManual ? "bg-accent" : "bg-line"
            }`}
          >
            <span
              aria-hidden
              className={`absolute top-0.5 h-4 w-4 rounded-full bg-surface transition-transform ${
                !isManual ? "translate-x-4" : "translate-x-0.5"
              }`}
            />
          </button>
        </div>
      </header>

      <section className="border-b border-line bg-surface px-6 py-3">
        <button
          type="button"
          onClick={() => setSummaryExpanded((v) => !v)}
          className="flex w-full items-center justify-between text-left text-xs font-medium uppercase tracking-[0.14em] text-muted"
        >
          <span>Resumo da conversa</span>
          <span aria-hidden>{summaryExpanded ? "▾" : "▸"}</span>
        </button>
        {summaryExpanded ? (
          <div className="mt-2">
            {conversation.summary ? (
              <>
                <p className="text-sm leading-relaxed text-ink">{conversation.summary}</p>
                {conversation.summary_generated_at ? (
                  <p className="mt-1 text-xs text-muted">
                    Gerado em {formatFullDateTime(conversation.summary_generated_at)}
                  </p>
                ) : null}
              </>
            ) : (
              <p className="text-sm text-muted">Nenhum resumo gerado ainda.</p>
            )}
            {summaryError ? (
              <p role="alert" className="mt-2 text-xs text-danger">
                {summaryError}
                {summaryError.startsWith("Saldo") ? (
                  <>
                    {" "}
                    <a href="/creditos" className="underline">
                      Comprar créditos
                    </a>
                  </>
                ) : null}
              </p>
            ) : null}
            <button
              type="button"
              onClick={() => void generateSummary()}
              disabled={summarizing || messages.length === 0}
              className="mt-2 rounded-sm border border-line px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
            >
              {summarizing
                ? "Gerando…"
                : conversation.summary
                  ? "Atualizar resumo"
                  : "Resumir conversa"}
            </button>
          </div>
        ) : null}
      </section>
```

- [ ] **Step 6: Rodar e ver passar**

Run: `cd apps/web && npx --yes pnpm@9 test -- ConversationThread`
Expected: PASS em todos.

- [ ] **Step 7: Rodar toda a suíte, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/lib/format.ts apps/web/src/components/ConversationThread.tsx apps/web/__tests__/ConversationThread.test.tsx
git commit -m "feat(web): switch de IA e resumo sob demanda na thread de conversa"
```

---

### Task 4: `CLAUDE.md` e verificação local ponta a ponta

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Atualizar o CLAUDE.md**

Na seção "Frontend", no item do "Painel de Conversas" (`/conversas`), adicionar ao final do texto do item: `O botão de takeover é um switch "IA respondendo" (mesmo `PATCH`, só reskin visual); cada conversa tem uma seção recolhível de resumo (resumo sob demanda via `POST /conversations/{id}/summary`, botão "Resumir conversa"/"Atualizar resumo", bloqueado com aviso quando o saldo de créditos está esgotado).`

Na linha do `api` em "Estado atual do repositório", acrescentar à lista de rotas implementadas: `POST /api/v1/conversations/{id}/summary` (resumo sob demanda, consumindo créditos).

Na seção "Agents Service", no resumo de endpoints existentes, acrescentar: `POST /summaries` (resumo de conversa sob demanda, chamada direta ao LLM sem grafo — usado pelo `api` na feature de resumo do painel de conversas).

Na seção "Billing / Créditos", no bloco "Regra de consumo", adicionar uma linha citando que o resumo de conversa também debita créditos pela mesma fórmula (`ceil(tokens/CREDIT_TOKENS_PER_CREDIT)`), com `related_message_id=None` — reaproveita a env já existente, mas agora replicada em `apps/api/app/core/config.py` (o `api`, não só o `worker`, também converte tokens em créditos, para a rota de resumo).

- [ ] **Step 2: Build e verificação local**

```bash
docker compose up -d --build agents api web
```

1. Ter uma conversa com pelo menos duas mensagens no seed de dev (via webhook simulado ou o playground de admin, se aplicável) — ou usar uma conversa já existente do ambiente local.
2. Login como tenant, abrir `/conversas`, selecionar a conversa.
3. Confirmar que o botão antigo "Assumir conversa" foi substituído pelo switch "IA respondendo" (ligado, verde/accent) e que clicar nele ainda funciona (troca para modo manual, badge "Atendimento manual" aparece, campo de resposta habilita).
4. Clicar em "Resumo da conversa" para expandir a seção — deve mostrar "Nenhum resumo gerado ainda." e o botão "Resumir conversa".
5. Clicar em "Resumir conversa" — botão muda para "Gerando…", depois mostra o texto do resumo e "Gerado em {data}"; botão passa a dizer "Atualizar resumo".
6. Verificar em `/creditos` (ou via `GET /api/v1/billing/balance`) que o saldo do tenant caiu pelo custo em créditos do resumo.
7. Zerar manualmente o saldo do tenant no banco (`UPDATE tenants SET credit_balance = 0 WHERE id = '<tenant_id>';`) e tentar gerar o resumo de novo — confirmar a mensagem "Saldo de créditos esgotado…" com o link "Comprar créditos" apontando pra `/creditos`; restaurar o saldo depois do teste.
8. Confirmar, numa conversa nova sem nenhuma mensagem (se possível simular), que o botão de resumo aparece desabilitado.

Expected: todos os passos funcionam; o passo 6 confirma que o débito de créditos da rota nova está correto de ponta a ponta (não só nos testes mockados).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: resumo de conversa sob demanda e switch de IA documentados no CLAUDE.md"
```
