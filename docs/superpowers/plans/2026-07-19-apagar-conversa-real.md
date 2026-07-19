# Apagar histórico de conversa real — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que o escritório apague permanentemente o histórico de uma conversa REAL de WhatsApp (não só de teste) pelo painel `/conversas`, generalizando o mecanismo de exclusão que já existe só para conversas de teste.

**Architecture:** O `DELETE /api/v1/conversations/{conversation_id}` sai de `test_conversations.py` (onde hoje só funciona para `is_test=true`) e passa a viver em `conversations.py`, sem restrição de origem. A lógica de exclusão (zerar `related_message_id` no ledger — agora nas duas tabelas, tenant e cliente final —, apagar mensagens, apagar a conversa, limpar o checkpoint do LangGraph no `agents` best-effort) fica inline na rota, seguindo o padrão já usado pelo resumo sob demanda no mesmo arquivo. No frontend, `ConversationThread.tsx` ganha um botão "Excluir conversa" espelhando o já existente em `TestConversationThread.tsx`.

**Tech Stack:** FastAPI + SQLAlchemy async (`apps/api`), Next.js + Vitest + Testing Library (`apps/web`).

## Global Constraints

- Nunca usar a palavra "tokens" em texto visível ao tenant — sempre "créditos" (não se aplica diretamente aqui, não há copy de créditos nesta feature, mas vale para qualquer string nova).
- Ação é destrutiva e irreversível — confirmação via `window.confirm` obrigatória no frontend, com o texto exato: *"Apagar todo o histórico desta conversa? Essa ação não pode ser desfeita — as mensagens serão excluídas permanentemente."*
- Créditos já consumidos nunca são estornados — a exclusão zera a referência (`related_message_id`) no ledger, nunca apaga ou modifica o lançamento de crédito em si.
- Isolamento por `tenant_id` via `get_tenant_session`/`get_current_tenant` continua obrigatório — a exclusão só pode alcançar conversas do tenant autenticado.

Spec de referência: `docs/superpowers/specs/2026-07-19-apagar-conversa-real-design.md`.

---

## Nota de resolução de uma ambiguidade da spec

A spec diz para remover, junto com a rota `DELETE`, "a checagem `if not conversation.is_test: raise 409` em `_get_test_conversation`". Essa checagem, porém, também é usada pela rota `POST /conversations/{id}/test-messages` (`send_test_message`), que **precisa continuar** recusando conversas reais com 409 — isso é comportamento coberto por `TestSendTestMessage.test_conversa_real_retorna_409` em `test_test_conversations_routes.py`, fora do escopo desta feature. A leitura correta: a checagem "sai" da rota `DELETE` porque a rota inteira é removida deste arquivo — `_get_test_conversation` (usada só por `send_test_message` a partir de agora) **não é tocada**. Isso é o que as tasks abaixo implementam.

---

### Task 1: Renomear `delete_playground_conversation` → `delete_agent_checkpoint` no client do `agents`

O nome atual sugere uso exclusivo do playground de admin, mas a função sempre foi genérica (só precisa de um `thread_id`). Generalizando o nome antes de reutilizá-la na exclusão de conversas reais evita confusão futura.

**Files:**
- Modify: `apps/api/app/clients/agents.py:75-86`
- Modify: `apps/api/app/services/playground.py:9,45`
- Modify: `apps/api/tests/unit/test_playground_service.py:84`

**Interfaces:**
- Produces: `async def delete_agent_checkpoint(thread_id: str) -> None` em `app.clients.agents` — mesma assinatura e comportamento (best-effort, loga `logger.warning` em `httpx.HTTPError`, nunca propaga).

- [ ] **Step 1: Renomear a função no client**

Em `apps/api/app/clients/agents.py`, substituir (linhas 75-86):

```python
async def delete_playground_conversation(thread_id: str) -> None:
    """DELETE /conversations/{thread_id} no agents — melhor esforço, loga e
    segue em caso de falha (é só higiene do checkpoint, não bloqueia o front)."""
    try:
        async with httpx.AsyncClient(
            base_url=settings.agents_service_url, timeout=_DELETE_TIMEOUT_SECONDS
        ) as client:
            await client.delete(f"/conversations/{thread_id}", headers=_auth_headers())
    except httpx.HTTPError as exc:
        logger.warning(
            "Falha ao apagar conversa do playground | thread_id=%s erro=%s", thread_id, exc
        )
```

por:

```python
async def delete_agent_checkpoint(thread_id: str) -> None:
    """DELETE /conversations/{thread_id} no agents — limpa o checkpoint do
    LangGraph. Melhor esforço: loga e segue em caso de falha, nunca bloqueia
    o chamador (usado tanto pelo playground de admin quanto pela exclusão de
    conversas reais/de teste do painel do tenant)."""
    try:
        async with httpx.AsyncClient(
            base_url=settings.agents_service_url, timeout=_DELETE_TIMEOUT_SECONDS
        ) as client:
            await client.delete(f"/conversations/{thread_id}", headers=_auth_headers())
    except httpx.HTTPError as exc:
        logger.warning(
            "Falha ao apagar checkpoint do agente | thread_id=%s erro=%s", thread_id, exc
        )
```

- [ ] **Step 2: Atualizar o chamador do playground de admin**

Em `apps/api/app/services/playground.py`, linha 9, trocar:

```python
from app.clients.agents import delete_playground_conversation, send_playground_message
```

por:

```python
from app.clients.agents import delete_agent_checkpoint, send_playground_message
```

E na linha 45, trocar:

```python
async def delete_conversation(tenant_id: uuid.UUID, session_id: str) -> None:
    await delete_playground_conversation(f"{tenant_id}:playground-{session_id}")
```

por:

```python
async def delete_conversation(tenant_id: uuid.UUID, session_id: str) -> None:
    await delete_agent_checkpoint(f"{tenant_id}:playground-{session_id}")
```

- [ ] **Step 3: Atualizar o teste do playground**

Em `apps/api/tests/unit/test_playground_service.py`, dentro de `TestDeleteConversation.test_monta_thread_id_com_prefixo_playground`, trocar:

```python
        monkeypatch.setattr("app.services.playground.delete_playground_conversation", delete_mock)
```

por:

```python
        monkeypatch.setattr("app.services.playground.delete_agent_checkpoint", delete_mock)
```

- [ ] **Step 4: Rodar a suíte do `api` para confirmar que nada mais referencia o nome antigo**

Run: `cd apps/api && uv run pytest tests/unit/test_playground_service.py tests/unit/test_platform_admin_playground.py -v` (se este segundo arquivo não existir, rodar `uv run pytest tests/unit -k playground -v`)

Expected: todos os testes passam. Se algum teste falhar referenciando `delete_playground_conversation`, buscar `grep -rn delete_playground_conversation apps/api` e ajustar o restante.

- [ ] **Step 5: Commit**

```bash
cd apps/api && git add app/clients/agents.py app/services/playground.py tests/unit/test_playground_service.py
git commit -m "refactor(api): renomeia delete_playground_conversation para delete_agent_checkpoint"
```

---

### Task 2: Remover a rota `DELETE` de `test_conversations.py` (sem tocar em `_get_test_conversation`)

**Files:**
- Modify: `apps/api/app/api/v1/test_conversations.py:72-79`
- Modify: `apps/api/app/services/test_conversations.py:1-17,106-125`
- Modify: `apps/api/tests/unit/test_test_conversations_routes.py:202-247`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces: nenhuma interface nova — esta task só remove código morto/duplicado, preparando o terreno para a Task 3.

- [ ] **Step 1: Remover a rota `DELETE` e sua função em `test_conversations.py`**

Em `apps/api/app/api/v1/test_conversations.py`, remover por completo o bloco (linhas 72-79):

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

(mantendo a linha em branco única entre `send_test_message` e `_get_test_conversation` — o resultado final deve ter `send_test_message` terminando em `return TestMessagesOut(...)`, uma linha em branco, e então `async def _get_test_conversation(...)` direto, sem a rota `DELETE` no meio.)

- [ ] **Step 2: Remover `delete_test_conversation` de `services/test_conversations.py` e os imports que ficam sem uso**

Em `apps/api/app/services/test_conversations.py`, trocar o topo do arquivo (linhas 1-17):

```python
"""Conversas de teste: o tenant conversa com os próprios agentes sem WhatsApp.

Diferente do playground do admin (efêmero), aqui tudo persiste em
conversations/messages e o consumo debita créditos normalmente — teste gasta
token real de LLM.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete as sql_delete
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.agents import delete_playground_conversation, send_playground_message
from app.models import Conversation, CreditTransaction, Message, Tenant
from app.services.pricing import calcular_creditos, get_current_pricing_config
```

por:

```python
"""Conversas de teste: o tenant conversa com os próprios agentes sem WhatsApp.

Diferente do playground do admin (efêmero), aqui tudo persiste em
conversations/messages e o consumo debita créditos normalmente — teste gasta
token real de LLM.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.agents import send_playground_message
from app.models import Conversation, CreditTransaction, Message, Tenant
from app.services.pricing import calcular_creditos, get_current_pricing_config
```

E remover por completo, no final do arquivo, a função (linhas 106-125 — do `async def delete_test_conversation` até o fim do arquivo):

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

O arquivo deve terminar em `send_test_message` (a função `async def send_test_message(...)` e seu corpo completo, do `Step 3` já lido — inalterada), sem nada depois dela.

- [ ] **Step 3: Remover a classe `TestDelete` inteira de `test_test_conversations_routes.py`**

Em `apps/api/tests/unit/test_test_conversations_routes.py`, remover por completo o bloco final (linhas 202-247):

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

O arquivo deve terminar na classe `TestSendTestMessage` (com seus 6 testes, inalterados), sem a classe `TestDelete` depois dela.

- [ ] **Step 4: Rodar a suíte de `test_conversations` para confirmar que nada quebrou**

Run: `cd apps/api && uv run pytest tests/unit/test_test_conversations_routes.py -v`

Expected: `TestCreate` (1 teste) + `TestSendTestMessage` (6 testes) passam, nenhum `TestDelete` listado (a classe não existe mais).

- [ ] **Step 5: Rodar `ruff check` para pegar imports não usados**

Run: `cd apps/api && uv run ruff check app/api/v1/test_conversations.py app/services/test_conversations.py`

Expected: sem erros (nenhum import sobrando — `uuid` e `HTTPException`/`status` continuam usados por `_get_test_conversation`/`create_test_conversation`/`send_test_message`).

- [ ] **Step 6: Commit**

```bash
cd apps/api && git add app/api/v1/test_conversations.py app/services/test_conversations.py tests/unit/test_test_conversations_routes.py
git commit -m "refactor(api): remove a rota DELETE de conversas de teste (migra pra Task 3)"
```

---

### Task 3: Adicionar `DELETE /conversations/{conversation_id}` generalizado em `conversations.py`

**Files:**
- Modify: `apps/api/app/api/v1/conversations.py:1-33` (imports), `:268-280` (fim do arquivo — nova rota)
- Test: `apps/api/tests/unit/test_conversations_routes.py`

**Interfaces:**
- Consumes: `delete_agent_checkpoint(thread_id: str) -> None` (Task 1), `_get_conversation(conversation_id, ctx, session) -> Conversation` (já existe em `conversations.py:268-279`, sem alteração de assinatura).
- Produces: rota `DELETE /api/v1/conversations/{conversation_id}` → `204 No Content` em sucesso, `404` se a conversa não existe/não pertence ao tenant. Funciona para qualquer `is_test` (real ou teste).

- [ ] **Step 1: Escrever os testes (falhando) em `test_conversations_routes.py`**

Em `apps/api/tests/unit/test_conversations_routes.py`, adicionar ao final do arquivo (após `TestGenerateSummary`, mantendo o mesmo padrão de fixtures `client`/`session`/`_conversation`/`_execute_returning` já definidos no topo do arquivo):

```python


class TestDeleteConversation:
    def test_apaga_conversa_real_com_sucesso(self, client, session, monkeypatch) -> None:
        checkpoint_mock = AsyncMock()
        monkeypatch.setattr(conversations_module, "delete_agent_checkpoint", checkpoint_mock)
        session.scalar.return_value = _conversation()

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 204
        session.delete.assert_awaited_once()
        session.commit.assert_awaited()
        checkpoint_mock.assert_awaited_once_with(f"{TENANT_ID}:5511999998888")

    def test_apaga_conversa_de_teste_tambem(self, client, session, monkeypatch) -> None:
        # A rota generalizada não distingue origem — conversa de teste também
        # pode ser apagada por aqui (o botão de teste continua existindo no
        # front, mas o backend não faz mais essa distinção).
        monkeypatch.setattr(conversations_module, "delete_agent_checkpoint", AsyncMock())
        session.scalar.return_value = _conversation(is_test=True)

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 204

    def test_desvincula_ledger_do_tenant_e_do_cliente_final_antes_de_apagar(
        self, client, session, monkeypatch
    ) -> None:
        monkeypatch.setattr(conversations_module, "delete_agent_checkpoint", AsyncMock())
        session.scalar.return_value = _conversation()

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 204
        # três executes: UPDATE credit_transactions, UPDATE
        # end_customer_credit_transactions e DELETE messages, nessa ordem.
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        tenant_ledger_idx = next(
            i for i, s in enumerate(statements) if "credit_transactions" in s and "end_customer" not in s
        )
        end_customer_ledger_idx = next(
            i for i, s in enumerate(statements) if "end_customer_credit_transactions" in s
        )
        delete_idx = next(i for i, s in enumerate(statements) if "DELETE FROM messages" in s)
        assert tenant_ledger_idx < delete_idx
        assert end_customer_ledger_idx < delete_idx

    def test_conversa_inexistente_retorna_404(self, client, session, monkeypatch) -> None:
        checkpoint_mock = AsyncMock()
        monkeypatch.setattr(conversations_module, "delete_agent_checkpoint", checkpoint_mock)
        session.scalar.return_value = None

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 404
        session.delete.assert_not_awaited()
        checkpoint_mock.assert_not_awaited()

    def test_falha_no_checkpoint_nao_impede_a_exclusao(self, client, session, monkeypatch) -> None:
        # delete_agent_checkpoint já engole a própria exceção (best-effort) —
        # aqui só confirmamos que a rota não depende do retorno dele.
        checkpoint_mock = AsyncMock(return_value=None)
        monkeypatch.setattr(conversations_module, "delete_agent_checkpoint", checkpoint_mock)
        session.scalar.return_value = _conversation()

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 204
        checkpoint_mock.assert_awaited_once()
```

- [ ] **Step 2: Rodar os testes novos e confirmar que falham (rota ainda não existe)**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py::TestDeleteConversation -v`

Expected: FAIL em todos — `405 Method Not Allowed` ou `AttributeError: module 'app.api.v1.conversations' has no attribute 'delete_agent_checkpoint'` (a rota/o import ainda não existem).

- [ ] **Step 3: Adicionar os imports novos em `conversations.py`**

Em `apps/api/app/api/v1/conversations.py`, trocar as linhas 8-30:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.agents import (
    AgentsApiError,
    AgentsNetworkError,
    generate_conversation_summary,
    sync_conversation_context,
)
from app.clients.whatsapp import WhatsAppSendError, send_text_message
from app.core.crypto import decrypt_access_token
from app.models import Conversation, CreditTransaction, Message, Tenant, WhatsAppNumber
from app.schemas.conversations import (
    ConversationOut,
    ConversationStateUpdate,
    ConversationUsageOut,
    MessageOut,
    SendMessageRequest,
)
from app.services.conversations_usage import build_conversations_usage
from app.services.pricing import calcular_creditos, get_current_pricing_config
```

por:

```python
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
```

- [ ] **Step 4: Adicionar a rota `DELETE`**

No mesmo arquivo, imediatamente antes de `async def _get_conversation(...)` (a função privada no fim do arquivo, hoje nas linhas 268-279), inserir:

```python
@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Apaga mensagens + conversa (real ou de teste); ledger fica (related_message_id
    vira NULL nas duas tabelas — tenant e cliente final —, o consumo continua
    auditável). Checkpoint no agents é limpado best-effort. Irreversível."""
    conversation = await _get_conversation(conversation_id, ctx, session)
    thread_id = f"{ctx.tenant_id}:{conversation.contact_phone_number}"
    logger.info(
        "Excluindo histórico de conversa | tenant_id=%s conversation_id=%s contact=%s",
        ctx.tenant_id,
        conversation.id,
        conversation.contact_phone_number,
    )

    message_ids = select(Message.id).where(Message.conversation_id == conversation.id)
    await session.execute(
        update(CreditTransaction)
        .where(CreditTransaction.related_message_id.in_(message_ids))
        .values(related_message_id=None)
    )
    await session.execute(
        update(EndCustomerCreditTransaction)
        .where(EndCustomerCreditTransaction.related_message_id.in_(message_ids))
        .values(related_message_id=None)
    )
    await session.execute(sql_delete(Message).where(Message.conversation_id == conversation.id))
    await session.delete(conversation)
    await session.commit()

    await delete_agent_checkpoint(thread_id)


```

- [ ] **Step 5: Rodar os testes novos de novo e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py::TestDeleteConversation -v`

Expected: PASS nos 5 testes.

- [ ] **Step 6: Rodar a suíte completa do `api` e o lint**

Run: `cd apps/api && uv run pytest tests/unit -v && uv run ruff check .`

Expected: todos os testes passam (nenhuma regressão em `test_test_conversations_routes.py`, `test_playground_service.py` ou qualquer outro), `ruff check` sem erros.

- [ ] **Step 7: Commit**

```bash
cd apps/api && git add app/api/v1/conversations.py tests/unit/test_conversations_routes.py
git commit -m "feat(api): generaliza DELETE /conversations/{id} para conversas reais"
```

---

### Task 4: Botão "Excluir conversa" em `ConversationThread.tsx`

**Files:**
- Modify: `apps/web/src/components/ConversationThread.tsx`
- Modify: `apps/web/src/components/ConversationsPanel.tsx:150-164`
- Test: `apps/web/__tests__/ConversationThread.test.tsx`

**Interfaces:**
- Consumes: `DELETE /api/v1/conversations/{id}` via `backendFetch` (já existe no backend após a Task 3).
- Produces: prop nova `onDeleted?: () => void` em `ConversationThreadProps` — opcional com no-op implícito (decisão de design: evita reescrever as ~18 chamadas de `<ConversationThread />` já existentes em `ConversationThread.test.tsx` que não exercitam exclusão; só o novo teste da Task 4 passa `onDeleted` explicitamente). `ConversationsPanel.tsx` passa a função real (`handleDeleted`, já existente e usada por `TestConversationThread`).

- [ ] **Step 1: Escrever os testes (falhando) em `ConversationThread.test.tsx`**

Em `apps/web/__tests__/ConversationThread.test.tsx`, adicionar ao final do `describe("ConversationThread", ...)` (depois do último teste, "não mostra o badge quando a mensagem foi entregue", antes do `});` de fechamento do describe):

```tsx

  it("exclui a conversa com confirmação e chama onDeleted", async () => {
    const onDeleted = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "DELETE") {
        return jsonResponse(null, 204);
      }
      return jsonResponse([]);
    });

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        onDeleted={onDeleted}
        pollMs={0}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Excluir conversa" }));

    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
    expect(window.confirm).toHaveBeenCalledWith(
      "Apagar todo o histórico desta conversa? Essa ação não pode ser desfeita — as mensagens serão excluídas permanentemente.",
    );
  });

  it("não exclui quando o usuário cancela a confirmação", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Excluir conversa" }));

    expect(
      backendFetchMock.mock.calls.some(([, init]) => init?.method === "DELETE"),
    ).toBe(false);
  });

  it("mostra erro quando a exclusão falha", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "DELETE") {
        return jsonResponse({ detail: "erro" }, 500);
      }
      return jsonResponse([]);
    });

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Excluir conversa" }));

    await waitFor(() =>
      expect(
        screen.getByText("Não foi possível excluir a conversa. Tente novamente."),
      ).toBeInTheDocument(),
    );
  });
```

- [ ] **Step 2: Rodar os testes novos e confirmar que falham**

Run: `cd apps/web && pnpm vitest run __tests__/ConversationThread.test.tsx`

Expected: FAIL nos 3 testes novos — `Unable to find role="button" with name "Excluir conversa"` (o botão ainda não existe).

- [ ] **Step 3: Adicionar a prop `onDeleted` e a função `handleDelete`**

Em `apps/web/src/components/ConversationThread.tsx`, trocar (linhas 9-19):

```tsx
interface ConversationThreadProps {
  conversation: Conversation;
  onConversationUpdate: (conversation: Conversation) => void;
  pollMs?: number;
}

export function ConversationThread({
  conversation,
  onConversationUpdate,
  pollMs = 4000,
}: ConversationThreadProps) {
```

por:

```tsx
interface ConversationThreadProps {
  conversation: Conversation;
  onConversationUpdate: (conversation: Conversation) => void;
  onDeleted?: () => void;
  pollMs?: number;
}

export function ConversationThread({
  conversation,
  onConversationUpdate,
  onDeleted,
  pollMs = 4000,
}: ConversationThreadProps) {
```

E, imediatamente antes de `const sendMessage = async (event: React.FormEvent) => {` (linha 136), inserir:

```tsx
  const handleDelete = async () => {
    if (
      !window.confirm(
        "Apagar todo o histórico desta conversa? Essa ação não pode ser desfeita — as mensagens serão excluídas permanentemente.",
      )
    ) {
      return;
    }
    setError(null);
    const response = await backendFetch(`conversations/${conversation.id}`, {
      method: "DELETE",
    });
    if (response.ok) {
      onDeleted?.();
    } else {
      setError("Não foi possível excluir a conversa. Tente novamente.");
    }
  };

```

- [ ] **Step 4: Adicionar o botão no cabeçalho**

No mesmo arquivo, dentro do `<header>`, trocar (linhas 181-200):

```tsx
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
```

por:

```tsx
        <div className="flex items-center gap-4">
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
          <button
            type="button"
            onClick={() => void handleDelete()}
            className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
          >
            Excluir conversa
          </button>
        </div>
      </header>
```

- [ ] **Step 5: Rodar os testes novos e confirmar que passam**

Run: `cd apps/web && pnpm vitest run __tests__/ConversationThread.test.tsx`

Expected: PASS em todos os testes do arquivo (os 3 novos + os já existentes, sem regressão — a prop `onDeleted` é opcional, então as chamadas antigas sem ela continuam válidas).

- [ ] **Step 6: Wire `onDeleted` em `ConversationsPanel.tsx`**

Em `apps/web/src/components/ConversationsPanel.tsx`, trocar (linhas 158-164):

```tsx
                <ConversationThread
                  key={selected.id}
                  conversation={selected}
                  onConversationUpdate={handleConversationUpdate}
                />
```

por:

```tsx
                <ConversationThread
                  key={selected.id}
                  conversation={selected}
                  onConversationUpdate={handleConversationUpdate}
                  onDeleted={() => handleDeleted(selected.id)}
                />
```

- [ ] **Step 7: Rodar a suíte completa do `web` e o lint**

Run: `cd apps/web && pnpm test && pnpm lint`

Expected: todos os testes passam (inclusive `ConversationsPanel.test.tsx`, sem regressão), lint sem erros.

- [ ] **Step 8: Commit**

```bash
cd apps/web && git add src/components/ConversationThread.tsx src/components/ConversationsPanel.tsx __tests__/ConversationThread.test.tsx
git commit -m "feat(web): botão de excluir conversa real no painel de conversas"
```

---

## Verificação final

- [ ] **Step 1: Rodar as duas suítes completas de novo, do zero**

Run: `cd apps/api && uv run pytest tests/unit -v && uv run ruff check .`
Run: `cd apps/web && pnpm test && pnpm lint && pnpm build`

Expected: tudo verde. `pnpm build` confirma que o TypeScript compila (prop opcional não quebra nenhum call site existente).

- [ ] **Step 2: Teste manual rápido (dev local)**

Com o stack local no ar (`docker compose up -d`), abrir `/conversas`, selecionar uma conversa real (ou criar uma via `scripts/seed_dev.py` se não houver nenhuma), clicar em "Excluir conversa", confirmar no `window.confirm`, e verificar que ela desaparece da lista e a seleção volta ao estado vazio.
