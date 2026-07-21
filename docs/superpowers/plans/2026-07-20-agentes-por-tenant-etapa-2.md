# Agentes por Tenant — Etapa 2 (motor dinâmico no `agents`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Substituir o grafo LangGraph fixo de 4 nós (secretária + 3 especialistas hardcoded) do `apps/agents` por um motor genérico de 2 nós que resolve o agente ativo a partir da lista de agentes do tenant (tabela `agents`, já modelada na Etapa 1), permitindo transferência dinâmica entre agentes (`transfer_to_agent`) e busca de conhecimento escopada por agente (`buscar_base_conhecimento_agente`), e propagar essa lista de ponta a ponta (`worker`/`api` → `agents` → `api_rag`).

**Architecture:** O `agents` service nunca acessa o Postgres principal (`advoxs`) — a lista de agentes do tenant (id, nome, instruções, `is_entry_point`, ids de arquivos de KB anexados) é resolvida pelo CHAMADOR (`worker` para mensagens reais, `api` para playground/conversas de teste) e injetada em `POST /messages` a cada chamada, no mesmo padrão já usado para `end_customer_billing`. Dentro do `agents`, os 4 nós fixos (`agente_secretaria`, `agente_condominial`, `agente_contratos`, `agente_direito_consumidor`) colapsam num único `agent_node` que resolve o agente ativo (`current_agent_id` no estado, com fallback pro `is_entry_point` quando `None` ou inválido) e monta prompt/tools a partir dos dados desse agente — o grafo perde as arestas condicionais (`route_from_start`/`route_from_tool_node`) porque só existe um nó de agente pra rotear. `transfer_to_agent` substitui `transfer_to_specialist` (valida o `agent_id` contra a lista real, nunca confiando no LLM); `buscar_base_conhecimento_agente` substitui as 3 tools fixas de categoria + `buscar_base_conhecimento_escritorio`, escopada aos `knowledge_base_file_ids` do agente ativo via um filtro opcional `doc_ids` novo no `api_rag`.

**Tech Stack:** FastAPI + LangGraph (`apps/agents`, Python 3.13), Arq (`apps/worker`), FastAPI + SQLAlchemy async (`apps/api`), FastAPI + Qdrant (`apps/api_rag`) — mesmas stacks já em uso, sem dependências novas.

## Global Constraints

- O `agents` service **nunca** ganha acesso direto ao Postgres `advoxs` — a lista de agentes chega sempre via `POST /messages`, nunca por query própria.
- O contrato de resposta `current_agent` de `POST /messages` **mantém o nome do campo**, mas passa a conter o **nome do agente** (ex: `"Secretária"`, `"Condominial"`, ou um nome customizado pelo tenant) em vez da chave interna do nó fixo (`"agente_secretaria"`) — isso evita qualquer mudança no frontend (`AdminPlaygroundPanel.tsx`, que já faz fallback `AGENT_LABELS[currentAgent] ?? currentAgent`) ou no schema `PlaygroundMessageOut`.
- `is_billing_blocked`, o gate de saldo esgotado do cliente final, e a tool `gerar_link_pagamento_cliente` mantêm exatamente o comportamento atual — só passam a ser avaliados dentro do `agent_node` genérico em vez de duplicados em 4 funções.
- A despedida de transferência (injetar uma frase de "vou te passar pra fulano" antes de ir pro `tool_node`) passa a se aplicar a **qualquer** agente que transfira sem `content` — isso fecha a pendência já documentada no CLAUDE.md ("despedida de transferência automática só implementada para secretária/condominial"), como consequência natural de colapsar os 4 nós em 1.
- `bucar_base_conhecimento_usuario` (busca nos documentos que o próprio contato enviou na conversa) **não muda** — fora de escopo desta etapa.
- Toda tool que precisa de um dado que só o sistema pode fornecer com segurança (`conversation_id`, `knowledge_base_file_ids` do agente ativo, `valid_agent_ids`, saldo/enabled do cliente final) continua sendo injetada no `tool_node`, nunca aceita como valor vindo do LLM — mesmo padrão de `STATE_SCOPED_TOOLS`/`BILLING_GATED_TOOLS` já existente.
- Conversas com checkpoint já existente ANTES do deploy desta etapa (campo antigo `current_specialist`) perdem o especialista fixado na próxima mensagem — o novo campo `current_agent_id` não existe nesses checkpoints e o `agent_node` cai no fallback (`is_entry_point`). É um efeito colateral aceito da migração (mesmo padrão de corte direto já usado neste projeto, ex: remoção do Chatwoot) — não requer código de migração de checkpoint.
- Nenhuma mudança de frontend (`apps/web`) nesta etapa — fica para a Etapa 3, já prevista no spec.

---

### Task 1: `apps/api_rag` — filtro opcional `doc_ids` no retrieval de usuário

**Files:**
- Modify: `apps/api_rag/clients/qdrant.py`
- Modify: `apps/api_rag/api/routes/retrievals.py`
- Test: `apps/api_rag/tests/unit/test_qdrant_client.py`
- Test: `apps/api_rag/tests/unit/test_routes.py`

**Interfaces:**
- Consumes: nada de tasks anteriores (task independente).
- Produces: `POST /retrieval/users` aceita `doc_ids: list[str] | None` no body — quando presente, restringe a busca a esses `doc_id`s (além do `tenant_id` obrigatório). Usado pela Task 2 (`retrieval_escritorio` no `agents`).

- [ ] **Step 1: Escrever o teste que falha para `_tenant_filter` com lista**

Em `apps/api_rag/tests/unit/test_qdrant_client.py`, adicionar o import de `MatchAny` e um novo teste na classe `TestTenantFilter`:

```python
from qdrant_client.models import MatchAny, PointStruct
```

(substituindo a linha `from qdrant_client.models import PointStruct` já existente no topo do arquivo por essa, com os dois nomes).

```python
    def test_extra_filters_lista_usa_match_any(self) -> None:
        f = _tenant_filter("t1", {"doc_id": ["d1", "d2"]})

        doc_condition = next(c for c in f.must if c.key == "doc_id")
        assert isinstance(doc_condition.match, MatchAny)
        assert doc_condition.match.any == ["d1", "d2"]
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `cd apps/api_rag && uv run pytest tests/unit/test_qdrant_client.py -v -k match_any`
Expected: FAIL — `AttributeError` ou `assert False` (o valor vira `MatchValue`, não `MatchAny`, porque `_tenant_filter` hoje só sabe montar `MatchValue`).

- [ ] **Step 3: Implementar o suporte a lista em `_tenant_filter`**

Em `apps/api_rag/clients/qdrant.py`, adicionar `MatchAny` ao import existente:

```python
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    Prefetch,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)
```

E trocar o corpo de `_tenant_filter`:

```python
def _tenant_filter(tenant_id: str, extra_filters: dict | None = None) -> Filter:
    """Monta o filtro com tenant_id obrigatório + condições extras.

    O tenant_id nunca é opcional nem decisão do chamador de alto nível
    (agente): sem ele, qualquer operação de busca/deleção falha aqui.
    Valores de extra_filters que sejam listas usam MatchAny (ex: doc_ids de
    um agente específico); valores escalares usam MatchValue como antes.
    """
    if not tenant_id:
        raise ValueError("tenant_id é obrigatório em todo acesso ao Qdrant")

    conditions = [FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]
    for key, value in (extra_filters or {}).items():
        if isinstance(value, list):
            conditions.append(FieldCondition(key=key, match=MatchAny(any=value)))
        else:
            conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
    return Filter(must=conditions)
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/api_rag && uv run pytest tests/unit/test_qdrant_client.py -v`
Expected: todos os testes do arquivo passam, incluindo o novo.

- [ ] **Step 5: Escrever o teste que falha para a rota `/retrieval/users`**

Em `apps/api_rag/tests/unit/test_routes.py`, adicionar dentro de `class TestRetrievalUsers` (depois de `test_busca_escopada_por_tenant_e_conversa`):

```python
    def test_repassa_doc_ids_quando_informado(self, client, retrieval_service) -> None:
        response = client.post(
            "/retrieval/users",
            json={
                "tenant_id": "t1",
                "conversation_id": "kb",
                "message": "regimento",
                "doc_ids": ["f1", "f2"],
            },
            headers=HEADERS,
        )

        assert response.status_code == 200
        retrieval_service.search_hybrid.assert_awaited_once_with(
            query="regimento",
            tenant_id="t1",
            extra_filters={"conversation_id": "kb", "doc_id": ["f1", "f2"]},
        )

    def test_sem_doc_ids_nao_inclui_filtro(self, client, retrieval_service) -> None:
        response = client.post(
            "/retrieval/users",
            json={"tenant_id": "t1", "conversation_id": "kb", "message": "regimento"},
            headers=HEADERS,
        )

        assert response.status_code == 200
        retrieval_service.search_hybrid.assert_awaited_once_with(
            query="regimento",
            tenant_id="t1",
            extra_filters={"conversation_id": "kb"},
        )
```

- [ ] **Step 6: Rodar e confirmar falha**

Run: `cd apps/api_rag && uv run pytest tests/unit/test_routes.py -v -k doc_ids`
Expected: FAIL em `test_repassa_doc_ids_quando_informado` — `422` (campo `doc_ids` não existe em `UsersRetrievalRequest`) ou `extra_filters` sem a chave `doc_id`.

- [ ] **Step 7: Implementar `doc_ids` na rota**

Em `apps/api_rag/api/routes/retrievals.py`, atualizar `UsersRetrievalRequest` e `retrieval_users`:

```python
class UsersRetrievalRequest(BaseModel):
    tenant_id: str
    conversation_id: str
    message: str
    doc_ids: list[str] | None = None
```

```python
@router_retrieval.post("/users")
async def retrieval_users(
    body: UsersRetrievalRequest,
    service: RetrievalService = Depends(get_retrieval),
    security: str = Depends(verify_api_key),
):
    """Busca nos documentos enviados pelo contato, escopada por tenant + conversa.

    `doc_ids`, quando informado, restringe a busca a esse subconjunto de
    documentos (ex: os arquivos anexados a um agente específico do escritório)
    — omitido, busca em todo o pool de documentos daquela conversation_id.
    """
    try:
        logger.info(
            f"Busca usuário | tenant={body.tenant_id} | conversa={body.conversation_id}"
            f" | mensagem={body.message}"
        )
        extra_filters = {"conversation_id": body.conversation_id}
        if body.doc_ids:
            extra_filters["doc_id"] = body.doc_ids
        results = await service.search_hybrid(
            query=body.message,
            tenant_id=body.tenant_id,
            extra_filters=extra_filters,
        )
        return {"results": results}
    except ValueError as e:
        logger.warning(f"Erro de busca de informações: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Erro ao buscar informações: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 8: Rodar e confirmar sucesso**

Run: `cd apps/api_rag && uv run pytest tests/unit -v`
Expected: todos os testes passam (nenhuma regressão nos existentes — `extra_filters` sem `doc_ids` continua `{"conversation_id": "..."}` exatamente como antes).

- [ ] **Step 9: Lint**

Run: `cd apps/api_rag && uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add apps/api_rag/clients/qdrant.py apps/api_rag/api/routes/retrievals.py apps/api_rag/tests/unit/test_qdrant_client.py apps/api_rag/tests/unit/test_routes.py
git commit -m "feat(api_rag): filtro opcional doc_ids no retrieval de usuário"
```

---

### Task 2: `apps/agents` — `transfer_to_agent` + `buscar_base_conhecimento_agente`

**Files:**
- Modify: `apps/agents/agents/tools.py`
- Modify: `apps/agents/clients/retrieval.py`
- Test: `apps/agents/tests/unit/test_tools.py`
- Test: `apps/agents/tests/unit/test_retrieval_client.py`

**Interfaces:**
- Consumes: nada de código de outra task (a Task 1 já entregou `doc_ids` no `api_rag`, mas este client só passa a usá-lo).
- Produces: `tools = [buscar_base_conhecimento_agente, bucar_base_conhecimento_usuario, gerar_link_pagamento_cliente, transfer_to_agent]` (lista exportada de `agents/tools.py`), `is_billing_blocked(enabled, balance) -> bool` (inalterada), consumidos pela Task 3 (`agent_node`/`tool_node`).

- [ ] **Step 1: Escrever os testes que falham para `retrieval_escritorio` com `doc_ids`**

Em `apps/agents/tests/unit/test_retrieval_client.py`, adicionar depois de `test_retrieval_escritorio_usa_conversation_id_kb`:

```python
async def test_retrieval_escritorio_inclui_doc_ids_quando_informado(monkeypatch) -> None:
    client = _mock_async_client(monkeypatch, {"results": []})

    await retrieval_escritorio("tenant-1:5511999998888", "regimento", doc_ids=["f1", "f2"])

    body = client.post.await_args.kwargs["json"]
    assert body["doc_ids"] == ["f1", "f2"]


async def test_retrieval_escritorio_sem_doc_ids_nao_inclui_chave(monkeypatch) -> None:
    client = _mock_async_client(monkeypatch, {"results": []})

    await retrieval_escritorio("tenant-1:5511999998888", "regimento")

    body = client.post.await_args.kwargs["json"]
    assert "doc_ids" not in body
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `cd apps/agents && uv run pytest tests/unit/test_retrieval_client.py -v -k doc_ids`
Expected: FAIL — `retrieval_escritorio()` não aceita o argumento `doc_ids` (`TypeError`).

- [ ] **Step 3: Implementar `doc_ids` em `retrieval_escritorio`**

Em `apps/agents/clients/retrieval.py`, substituir a função `retrieval_escritorio` por:

```python
async def retrieval_escritorio(
    conversation_id: str, message: str, doc_ids: list[str] | None = None
) -> list[dict]:
    """Busca na base de conhecimento própria do escritório (tenant).

    Args:
        conversation_id: thread_id composto "{tenant_id}:{contact_phone_number}" —
            só o tenant_id é usado; a busca é sempre em conversation_id="kb".
        message: Pergunta do usuário.
        doc_ids: quando informado, restringe a busca a esses ids de
            knowledge_base_files — a base anexada a um agente específico.
            Omitido, busca no pool inteiro do tenant.
    """
    tenant_id, _, _ = str(conversation_id).partition(":")

    payload = {
        "tenant_id": tenant_id,
        "conversation_id": KB_CONVERSATION_ID,
        "message": message,
    }
    if doc_ids:
        payload["doc_ids"] = doc_ids

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{RAG_API_URL}/retrieval/users",
                json=payload,
                headers=HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            logger.debug("Retrieval escritório retornou {} chunks | tenant={}", len(results), tenant_id)
            return results

    except httpx.HTTPStatusError as e:
        logger.error("Erro HTTP no retrieval escritório | status={} | response={}", e.response.status_code, e.response.text)
        return []
    except Exception as e:
        logger.error("Erro ao consultar retrieval escritório | error={}", str(e))
        return []
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/agents && uv run pytest tests/unit/test_retrieval_client.py -v`
Expected: todos passam.

- [ ] **Step 5: Escrever os testes que falham para as novas tools**

Em `apps/agents/tests/unit/test_tools.py`, primeiro trocar o bloco de imports do topo (remove as 3 tools de categoria + `transfer_to_specialist`, adiciona as novas):

```python
import pytest
import requests
from unittest.mock import AsyncMock, patch, MagicMock
from langgraph.types import Command
from agents.tools import (
    transfer_to_agent,
    buscar_base_conhecimento_agente,
    bucar_base_conhecimento_usuario,
    enviar_documento,
    gerar_link_pagamento_cliente,
)
```

Substituir TODO o bloco de testes de `transfer_to_specialist` (de `# transfer_to_specialist` até o fim do bloco `test_transfer_sem_billing_habilitado_ignora_saldo`) e TODO o bloco dos 3 `bucar_base_conhecimento_{condominial,contratos,direito_consumidor}` (das três seções `# bucar_base_conhecimento_condominial` / `_contratos` / `_direito_consumidor`) pelo seguinte:

```python
# ──────────────────────────────────────────────
# transfer_to_agent
# ──────────────────────────────────────────────

def test_transfer_retorna_command():
    result = transfer_to_agent.invoke({"agent_id": "agent-2", "valid_agent_ids": ["agent-2"]})
    assert isinstance(result, Command)


def test_transfer_atualiza_current_agent_id():
    result = transfer_to_agent.invoke({"agent_id": "agent-2", "valid_agent_ids": ["agent-2"]})
    assert result.update["current_agent_id"] == "agent-2"


def test_transfer_ativa_receptive_message():
    result = transfer_to_agent.invoke({"agent_id": "agent-2", "valid_agent_ids": ["agent-2"]})
    assert result.update["receptive_message_specialist"] is True


def test_transfer_agent_id_fora_da_lista_recusa():
    result = transfer_to_agent.invoke({"agent_id": "agent-forjado", "valid_agent_ids": ["agent-2"]})
    assert isinstance(result, str)
    assert "recusada" in result.lower()


def test_transfer_sem_valid_agent_ids_recusa():
    result = transfer_to_agent.invoke({"agent_id": "agent-2"})
    assert isinstance(result, str)
    assert "recusada" in result.lower()


def test_transfer_bloqueada_sem_saldo_retorna_string():
    result = transfer_to_agent.invoke(
        {
            "agent_id": "agent-2",
            "valid_agent_ids": ["agent-2"],
            "end_customer_billing_enabled": True,
            "end_customer_balance": 0,
        }
    )
    assert isinstance(result, str)
    assert "bloqueada" in result.lower()


def test_transfer_liberada_com_saldo_positivo():
    result = transfer_to_agent.invoke(
        {
            "agent_id": "agent-2",
            "valid_agent_ids": ["agent-2"],
            "end_customer_billing_enabled": True,
            "end_customer_balance": 100,
        }
    )
    assert isinstance(result, Command)
    assert result.update["current_agent_id"] == "agent-2"


def test_transfer_sem_billing_habilitado_ignora_saldo():
    result = transfer_to_agent.invoke(
        {
            "agent_id": "agent-2",
            "valid_agent_ids": ["agent-2"],
            "end_customer_billing_enabled": False,
            "end_customer_balance": 0,
        }
    )
    assert isinstance(result, Command)


# ──────────────────────────────────────────────
# buscar_base_conhecimento_agente
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_buscar_agente_chama_retrieval_com_doc_ids():
    with patch("agents.tools.retrieval_escritorio", new=AsyncMock(return_value="resultado")) as mock_fn:
        result = await buscar_base_conhecimento_agente.ainvoke({
            "query": "regimento",
            "conversation_id": "tenant-1:5511999998888",
            "knowledge_base_file_ids": ["f1", "f2"],
        })

        mock_fn.assert_called_once_with(
            "tenant-1:5511999998888", "regimento", doc_ids=["f1", "f2"]
        )
        assert result == "resultado"


@pytest.mark.asyncio
async def test_buscar_agente_sem_arquivos_nao_chama_retrieval():
    with patch("agents.tools.retrieval_escritorio", new=AsyncMock()) as mock_fn:
        result = await buscar_base_conhecimento_agente.ainvoke({
            "query": "regimento",
            "conversation_id": "tenant-1:5511999998888",
            "knowledge_base_file_ids": [],
        })

        mock_fn.assert_not_called()
        assert "não tem" in result.lower()


@pytest.mark.asyncio
async def test_buscar_agente_sem_knowledge_base_file_ids_nao_chama_retrieval():
    with patch("agents.tools.retrieval_escritorio", new=AsyncMock()) as mock_fn:
        result = await buscar_base_conhecimento_agente.ainvoke({
            "query": "regimento",
            "conversation_id": "tenant-1:5511999998888",
        })

        mock_fn.assert_not_called()
        assert "não tem" in result.lower()
```

Manter inalterados os blocos de `bucar_base_conhecimento_usuario`, `enviar_documento` e `gerar_link_pagamento_cliente` já existentes no arquivo.

- [ ] **Step 6: Rodar e confirmar falha**

Run: `cd apps/agents && uv run pytest tests/unit/test_tools.py -v`
Expected: FAIL em todos os testes novos — `ImportError` (`transfer_to_agent`/`buscar_base_conhecimento_agente` não existem ainda em `agents.tools`).

- [ ] **Step 7: Implementar as novas tools**

Em `apps/agents/agents/tools.py`, trocar o import de `clients.retrieval` (que hoje traz `retrieval_sistema, retrieval_usuario, retrieval_escritorio`) por:

```python
from clients.retrieval import retrieval_usuario, retrieval_escritorio
```

Remover as 3 tools `bucar_base_conhecimento_condominial`, `bucar_base_conhecimento_contratos`, `bucar_base_conhecimento_direito_consumidor` e a tool `buscar_base_conhecimento_escritorio` inteiras (do `@tool("bucar_base_conhecimento_condominial")` até o fim do corpo de `buscar_base_conhecimento_escritorio`, inclusive as duas linhas em branco entre elas).

No lugar delas (mantendo `bucar_base_conhecimento_usuario` exatamente como está, antes e depois), adicionar:

```python
@tool("buscar_base_conhecimento_agente")
async def buscar_base_conhecimento_agente(
    query: str,
    conversation_id: str,
    knowledge_base_file_ids: list[str] | None = None,
) -> str:
    """Busca na base de conhecimento anexada a este agente.

    Use quando a pergunta envolver documentos, materiais, modelos ou
    orientações que você tenha na sua própria base de conhecimento — cada
    agente só tem acesso aos arquivos que foram anexados especificamente a
    ele, nunca à base de outro agente.

    Args:
        query: Pergunta ou tema a ser pesquisado.
        conversation_id: preenchido automaticamente pelo sistema.
        knowledge_base_file_ids: preenchido automaticamente pelo sistema.
    """
    if not knowledge_base_file_ids:
        return "Este agente não tem nenhuma base de conhecimento anexada."
    return await retrieval_escritorio(conversation_id, query, doc_ids=knowledge_base_file_ids)
```

Substituir a função `transfer_to_specialist` (do `@tool("transfer_to_specialist")` até o fim do seu corpo) por:

```python
@tool("transfer_to_agent")
def transfer_to_agent(
    agent_id: str,
    valid_agent_ids: list[str] | None = None,
    end_customer_billing_enabled: bool = False,
    end_customer_balance: float = 0,
) -> str:
    """
    Transfere a conversa para outro agente do escritório.

    Args:
        agent_id: id do agente de destino — escolha entre os agentes
            disponíveis no seu contexto, nunca invente um id.
        valid_agent_ids: preenchido automaticamente pelo sistema.
        end_customer_billing_enabled: preenchido automaticamente pelo sistema.
        end_customer_balance: preenchido automaticamente pelo sistema.
    """
    if agent_id not in (valid_agent_ids or []):
        return (
            "Transferência recusada: agent_id inválido — escolha um dos agentes "
            "disponíveis no seu contexto."
        )
    if is_billing_blocked(end_customer_billing_enabled, end_customer_balance):
        return (
            "Transferência bloqueada: o cliente ainda não tem créditos disponíveis. "
            "Ofereça os pacotes de crédito e gere o link de pagamento antes de "
            "transferir para outro agente."
        )
    return Command(
        update={
            "current_agent_id": agent_id,
            "receptive_message_specialist": True,
        }
    )
```

Atualizar a lista `tools` no fim do arquivo:

```python
tools = [
    buscar_base_conhecimento_agente,
    bucar_base_conhecimento_usuario,
    gerar_link_pagamento_cliente,
    transfer_to_agent,
]
```

`enviar_documento`, `bucar_base_conhecimento_usuario`, `gerar_link_pagamento_cliente` e `is_billing_blocked` continuam exatamente como estão hoje (não remover nem alterar).

- [ ] **Step 8: Rodar e confirmar sucesso**

Run: `cd apps/agents && uv run pytest tests/unit/test_tools.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 9: Lint**

Run: `cd apps/agents && uv run ruff check agents/tools.py clients/retrieval.py tests/unit/test_tools.py tests/unit/test_retrieval_client.py`
Expected: `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add apps/agents/agents/tools.py apps/agents/clients/retrieval.py apps/agents/tests/unit/test_tools.py apps/agents/tests/unit/test_retrieval_client.py
git commit -m "feat(agents): transfer_to_agent e buscar_base_conhecimento_agente substituem as tools fixas"
```

---

### Task 3: `apps/agents` — `agent_node` genérico + grafo de 2 nós

**Files:**
- Modify: `apps/agents/agents/nodes.py`
- Modify: `apps/agents/agents/workflow.py`
- Modify: `apps/agents/tests/factories.py`
- Modify: `apps/agents/tests/unit/test_nodes.py`
- Delete: `apps/agents/tests/unit/test_graph_routing.py`
- Delete: `apps/agents/agents/prompts/secretaria.md`
- Delete: `apps/agents/agents/prompts/condominial.md`
- Delete: `apps/agents/agents/prompts/contratos.md`
- Delete: `apps/agents/agents/prompts/direito_consumidor.md`

**Interfaces:**
- Consumes: `tools`, `transfer_to_agent`, `buscar_base_conhecimento_agente`, `bucar_base_conhecimento_usuario`, `gerar_link_pagamento_cliente`, `is_billing_blocked` de `agents.tools` (Task 2).
- Produces: `agent_node(state: dict) -> Command`, `tool_node(state: dict) -> dict`, `graph` (StateGraph compilável) — consumidos pela Task 4 (`call_agent.py`).
- Novo formato de estado: `current_agent_id: str | None` (substitui `current_specialist`), novo campo `agents: list[dict]` — cada item `{"id": str, "name": str, "instructions": str, "is_entry_point": bool, "knowledge_base_file_ids": list[str]}`.

- [ ] **Step 1: Atualizar `tests/factories.py` pro novo formato de estado**

Substituir a função `base_state` em `apps/agents/tests/factories.py` por:

```python
def base_state(**overrides) -> dict:
    state = {
        "messages": [HumanMessage(content="mensagem de teste")],
        "num_before_messages": 10,
        "attachments": [],
        "conversation_id": "conv-test",
        "current_agent_id": None,
        "receptive_message_specialist": False,
        "agents": [
            {
                "id": "entry-1",
                "name": "Secretária",
                "instructions": "Você é a secretária de triagem.",
                "is_entry_point": True,
                "knowledge_base_file_ids": [],
            },
            {
                "id": "other-1",
                "name": "Condominial",
                "instructions": "Você é o especialista condominial.",
                "is_entry_point": False,
                "knowledge_base_file_ids": ["kb-1"],
            },
        ],
    }
    state.update(overrides)
    return state
```

(o resto do arquivo — `ai_with_tool_call`, `ai_response`, `mock_model` — não muda.)

- [ ] **Step 2: Escrever o novo `test_nodes.py` (RED)**

Substituir TODO o conteúdo de `apps/agents/tests/unit/test_nodes.py` por:

```python
import pytest
from unittest.mock import AsyncMock, patch
from langchain_core.messages import AIMessage
from langgraph.graph import END
from tests.factories import ai_with_tool_call, ai_response, mock_model, base_state

import agents.tools as tools_module


# ──────────────────────────────────────────────
# agent_node — ponto de entrada (current_agent_id=None)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entry_point_sem_tool_calls_vai_para_end():
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response("Olá, como posso ajudar?"))):
        result = await agent_node(base_state())

    assert result.goto == END
    assert result.update["current_agent_id"] == "entry-1"


@pytest.mark.asyncio
async def test_entry_point_com_tool_call_vai_para_tool_node():
    from agents.nodes import agent_node
    fake = ai_with_tool_call("transfer_to_agent", {"agent_id": "other-1"})

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(base_state())

    assert result.goto == "tool_node"


@pytest.mark.asyncio
async def test_current_agent_id_invalido_cai_no_ponto_de_entrada():
    """Um current_agent_id que não existe mais na lista (agente apagado,
    checkpoint de antes do deploy) cai no fallback do ponto de entrada."""
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response("oi"))):
        result = await agent_node(base_state(current_agent_id="agente-apagado"))

    assert result.update["current_agent_id"] == "entry-1"


@pytest.mark.asyncio
async def test_sem_agentes_no_estado_retorna_erro_generico():
    from agents.nodes import agent_node

    result = await agent_node(base_state(agents=[]))

    assert result.goto == END
    assert result.update["messages"][0].content != ""


@pytest.mark.asyncio
async def test_transfer_sem_content_injeta_despedida_com_nome_do_agente():
    from agents.nodes import agent_node
    fake = ai_with_tool_call("transfer_to_agent", {"agent_id": "other-1"}, content="")

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(base_state())

    ai_msg = result.update["messages"][0]
    assert ai_msg.content != "", "Despedida não foi injetada"
    assert "condominial" in ai_msg.content.lower()


@pytest.mark.asyncio
async def test_transfer_com_content_nao_sobrescreve():
    from agents.nodes import agent_node
    fake = ai_with_tool_call(
        "transfer_to_agent", {"agent_id": "other-1"}, content="Um momento, vou transferir você."
    )

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(base_state())

    ai_msg = result.update["messages"][0]
    assert ai_msg.content == "Um momento, vou transferir você."


@pytest.mark.asyncio
async def test_transfer_tool_call_mantem_tool_calls_na_mensagem():
    from agents.nodes import agent_node
    fake = ai_with_tool_call("transfer_to_agent", {"agent_id": "other-1"})

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(base_state())

    ai_msg = result.update["messages"][0]
    assert ai_msg.tool_calls, "tool_calls não foram preservados na mensagem"
    assert ai_msg.tool_calls[0]["name"] == "transfer_to_agent"


@pytest.mark.asyncio
async def test_bind_inclui_gerar_link_pagamento_quando_billing_habilitado(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agent_node(base_state(end_customer_billing={"enabled": True, "balance": 500, "packages": []}))

    bound_tools = model.bind_tools.call_args.args[0]
    tool_names = {t.name for t in bound_tools}
    assert "gerar_link_pagamento_cliente" in tool_names


@pytest.mark.asyncio
async def test_bind_nao_inclui_gerar_link_pagamento_quando_billing_desabilitado(monkeypatch) -> None:
    """A mera presença da tool no bind_tools já muda o comportamento de
    function-calling do modelo (visto num teste de integração real) — por
    isso ela só entra na lista quando a feature está de fato ligada pro
    tenant, nunca incondicionalmente."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agent_node(base_state(end_customer_billing={"enabled": False, "balance": 0, "packages": []}))

    bound_tools = model.bind_tools.call_args.args[0]
    tool_names = {t.name for t in bound_tools}
    assert "gerar_link_pagamento_cliente" not in tool_names


@pytest.mark.asyncio
async def test_bind_nao_inclui_gerar_link_pagamento_sem_end_customer_billing_no_state(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agent_node(base_state())

    bound_tools = model.bind_tools.call_args.args[0]
    tool_names = {t.name for t in bound_tools}
    assert "gerar_link_pagamento_cliente" not in tool_names


@pytest.mark.asyncio
async def test_injeta_pacotes_no_prompt_quando_sem_saldo_no_ponto_de_entrada(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": True,
            "balance": 0,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agent_node(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" in prompt_arg.content
    assert "p-1" in prompt_arg.content


@pytest.mark.asyncio
async def test_nao_injeta_pacotes_quando_billing_desabilitado(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": False,
            "balance": 0,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agent_node(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" not in prompt_arg.content
    assert "Pacotes disponíveis" not in prompt_arg.content


@pytest.mark.asyncio
async def test_nao_injeta_pacotes_com_saldo_positivo(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": True,
            "balance": 500,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agent_node(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" not in prompt_arg.content


@pytest.mark.asyncio
async def test_transfer_sem_content_pula_despedida_quando_bloqueado():
    """Quando a transferência vai ser bloqueada (sem saldo), não injeta despedida —
    o tool_node ainda vai rodar e transfer_to_agent vai recusar, então a despedida
    ("vou te passar agora") ficaria contraditória."""
    from agents.nodes import agent_node
    fake = ai_with_tool_call("transfer_to_agent", {"agent_id": "other-1"}, content="")

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(
            base_state(end_customer_billing={"enabled": True, "balance": 0, "packages": []})
        )

    ai_msg = result.update["messages"][0]
    assert ai_msg.content == ""
    assert ai_msg.tool_calls, "tool_calls devem ser preservados mesmo sem despedida"
    assert result.goto == "tool_node"


# ──────────────────────────────────────────────
# agent_node — agente não-entry-point (equivalente aos especialistas de antes)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agente_atual_sem_tool_calls_sem_content_vai_para_end():
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response(""))):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=False))

    assert result.goto == END
    assert result.update["current_agent_id"] == "other-1"


@pytest.mark.asyncio
async def test_agente_atual_com_tool_call_vai_para_tool_node():
    from agents.nodes import agent_node
    fake = ai_with_tool_call("transfer_to_agent", {"agent_id": "entry-1"})

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=False))

    assert result.goto == "tool_node"


@pytest.mark.asyncio
async def test_agente_atual_com_content_sem_tool_vai_para_end():
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response("Vou te ajudar com o condomínio."))):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=False))

    assert result.goto == END


@pytest.mark.asyncio
async def test_agente_atual_first_run_reseta_flag():
    """receptive_message_specialist deve ser False no update após first_run=True."""
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response("Olá! Sou o especialista condominial."))):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=True))

    assert result.update.get("receptive_message_specialist") is False


@pytest.mark.asyncio
async def test_agente_atual_nao_first_run_nao_inclui_flag_no_update():
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response(""))):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=False))

    assert "receptive_message_specialist" not in result.update


@pytest.mark.asyncio
async def test_ponto_de_entrada_nunca_recebe_instrucao_de_first_run(monkeypatch):
    """O ponto de entrada nunca ganha a instrução de 'primeira resposta' — mesmo
    que receptive_message_specialist venha True por engano no estado."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agent_node(base_state(current_agent_id=None, receptive_message_specialist=True))

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "primeira resposta" not in prompt_arg.content.lower()


@pytest.mark.asyncio
async def test_agente_bloqueado_por_saldo_esgotado_e_atendido_pelo_ponto_de_entrada(monkeypatch):
    """Saldo esgotado no meio da conversa (não só na transferência inicial) deve
    ser atendido pelo ponto de entrada (equivalente à antiga secretária), que
    oferece os pacotes — em vez de deixar o agente atual responder de graça."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("aqui estão os pacotes disponíveis"))
    monkeypatch.setattr("agents.nodes.model", model)

    result = await agent_node(
        base_state(
            current_agent_id="other-1",
            receptive_message_specialist=False,
            end_customer_billing={
                "enabled": True,
                "balance": 0,
                "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
            },
        )
    )

    assert result.update["current_agent_id"] == "entry-1"
    model.bind_tools.assert_called_once()
    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" in prompt_arg.content


@pytest.mark.asyncio
async def test_agente_com_saldo_positivo_nao_e_bloqueado():
    """Billing habilitado mas com saldo positivo não deve bloquear — fluxo normal."""
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response("Analisando seu caso."))):
        result = await agent_node(
            base_state(
                current_agent_id="other-1",
                receptive_message_specialist=False,
                end_customer_billing={"enabled": True, "balance": 500, "packages": []},
            )
        )

    assert result.goto == END
    assert result.update["current_agent_id"] == "other-1"
    assert result.update["messages"][0].content == "Analisando seu caso."


@pytest.mark.asyncio
async def test_agente_sem_billing_no_state_nao_bloqueia():
    """Sem end_customer_billing no state (fluxo normal de escritório, sem
    cobrança de cliente final), o agente segue chamando o LLM normalmente."""
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response("Vou orientar você."))):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=False))

    assert result.goto == END
    assert result.update["messages"][0].content == "Vou orientar você."


@pytest.mark.asyncio
async def test_transfer_sem_content_injeta_despedida_no_agente_atual():
    from agents.nodes import agent_node
    fake = ai_with_tool_call("transfer_to_agent", {"agent_id": "entry-1"}, content="")

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=False))

    ai_msg = result.update["messages"][0]
    assert ai_msg.content != ""
    assert "secretária" in ai_msg.content.lower()


# ──────────────────────────────────────────────
# tool_node — injeção de conversation_id, KB do agente e transferência
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_node_injeta_conversation_id_do_estado(monkeypatch) -> None:
    from agents.nodes import tool_node

    retrieval = AsyncMock(return_value=[])
    monkeypatch.setattr(tools_module, "retrieval_usuario", retrieval)

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "bucar_base_conhecimento_usuario",
                # O LLM tentou passar outro id — deve ser ignorado.
                "args": {"query": "meu contrato", "conversation_id": "tenant-malicioso:123"},
                "id": "call-1",
            }
        ],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-real:5511999998888",
        "agents": base_state()["agents"],
    }

    await tool_node(state)

    retrieval.assert_awaited_once_with("tenant-real:5511999998888", "meu contrato")


@pytest.mark.asyncio
async def test_tool_node_injeta_knowledge_base_file_ids_do_agente_atual(monkeypatch) -> None:
    from agents.nodes import tool_node

    retrieval = AsyncMock(return_value=[])
    monkeypatch.setattr(tools_module, "retrieval_escritorio", retrieval)

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "buscar_base_conhecimento_agente",
                # O LLM tentou passar outros ids — deve ser ignorado.
                "args": {"query": "regimento", "knowledge_base_file_ids": ["arquivo-forjado"]},
                "id": "call-1",
            }
        ],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-real:5511999998888",
        "current_agent_id": "other-1",
        "agents": base_state()["agents"],
    }

    await tool_node(state)

    retrieval.assert_awaited_once_with(
        "tenant-real:5511999998888", "regimento", doc_ids=["kb-1"]
    )


@pytest.mark.asyncio
async def test_tool_node_injeta_valid_agent_ids_em_transfer_to_agent() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "transfer_to_agent",
                # O LLM tentou transferir pra um id que não existe.
                "args": {"agent_id": "agente-forjado"},
                "id": "call-1",
            }
        ],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-1:5511999998888",
        "agents": base_state()["agents"],
    }

    result = await tool_node(state)

    assert "recusada" in result["messages"][0].content.lower()
    assert "current_agent_id" not in result


@pytest.mark.asyncio
async def test_tool_node_transfer_to_agent_valido_atualiza_estado() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[{"name": "transfer_to_agent", "args": {"agent_id": "other-1"}, "id": "call-1"}],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-1:5511999998888",
        "agents": base_state()["agents"],
    }

    result = await tool_node(state)

    assert result["current_agent_id"] == "other-1"
    assert result["receptive_message_specialist"] is True


@pytest.mark.asyncio
async def test_tool_node_injeta_saldo_do_cliente_final_em_transfer_to_agent() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "transfer_to_agent",
                # O LLM tentou passar saldo positivo — deve ser ignorado.
                "args": {"agent_id": "other-1", "end_customer_balance": 9999},
                "id": "call-1",
            }
        ],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-1:5511999998888",
        "agents": base_state()["agents"],
        "end_customer_billing": {"enabled": True, "balance": 0, "packages": []},
    }

    result = await tool_node(state)

    assert "bloqueada" in result["messages"][0].content.lower()


@pytest.mark.asyncio
async def test_tool_node_sem_end_customer_billing_no_state_nao_bloqueia() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[{"name": "transfer_to_agent", "args": {"agent_id": "other-1"}, "id": "call-1"}],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-1:5511999998888",
        "agents": base_state()["agents"],
    }

    result = await tool_node(state)

    assert result.get("current_agent_id") == "other-1"
```

- [ ] **Step 3: Rodar e confirmar falha**

Run: `cd apps/agents && uv run pytest tests/unit/test_nodes.py -v`
Expected: FAIL em praticamente todos — `agent_node` ainda não existe em `agents.nodes` (só existem `agente_secretaria`/`agente_condominial`/etc.).

- [ ] **Step 4: Deletar `apps/agents/tests/unit/test_graph_routing.py`**

```bash
rm apps/agents/tests/unit/test_graph_routing.py
```

(as funções que ele testava, `route_from_start`/`route_from_tool_node`, deixam de existir no Step 6 — o grafo de 2 nós não precisa de roteamento condicional.)

- [ ] **Step 5: Implementar `agent_node` e `tool_node` genéricos**

Substituir TODO o conteúdo de `apps/agents/agents/nodes.py` por:

```python
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from agents.helpers import strip_messages
from agents.tools import *

from dotenv import load_dotenv
from langgraph.graph import END
from langgraph.types import Command
from loguru import logger

load_dotenv()

model = ChatOpenAI(model="gpt-5-mini-2025-08-07", temperature=0)

# Tools cujo conversation_id vem SEMPRE do estado do grafo, nunca do LLM —
# o tenant_id vive dentro dele (isolamento multi-tenant).
STATE_SCOPED_TOOLS = {
    "bucar_base_conhecimento_usuario",
    "buscar_base_conhecimento_agente",
    "gerar_link_pagamento_cliente",
}
# Saldo/enabled do cliente final: nunca confiar em valor vindo do LLM.
BILLING_GATED_TOOLS = {"transfer_to_agent"}


async def agent_node(state: dict) -> Command:
    agents_by_id = {a["id"]: a for a in state.get("agents", [])}
    entry_point = next((a for a in state.get("agents", []) if a.get("is_entry_point")), None)

    if not agents_by_id or entry_point is None:
        logger.error("Nenhum agente disponível no estado — tenant sem agentes configurados")
        return Command(
            update={
                "messages": [
                    AIMessage(content="Desculpe, houve um erro ao processar sua mensagem.")
                ]
            },
            goto=END,
        )

    current_agent_id = state.get("current_agent_id")
    current = agents_by_id.get(current_agent_id) if current_agent_id else None
    if current is None:
        current = entry_point

    billing = state.get("end_customer_billing") or {}
    billing_enabled = bool(billing.get("enabled"))
    billing_blocked = is_billing_blocked(billing.get("enabled"), billing.get("balance", 0))

    if billing_blocked and not current["is_entry_point"]:
        logger.info(
            "Agente bloqueado por saldo esgotado, devolvendo pro ponto de entrada | agent_id={}",
            current["id"],
        )
        current = entry_point

    is_entry_point = current["is_entry_point"]
    # O ponto de entrada nunca recebe a instrução de "primeira resposta" —
    # esse conceito é só do agente que ACABOU de receber uma transferência
    # (equivalente à antiga distinção secretária vs. especialista).
    is_first_run = bool(state.get("receptive_message_specialist", False)) and not is_entry_point

    logger.info(
        "agent_node chamado | agent_id={} | mensagens={} | histórico={} | first_run={}",
        current["id"],
        len(state["messages"]),
        state["num_before_messages"],
        is_first_run,
    )

    last_messages = strip_messages(state["messages"], state["num_before_messages"])

    # gerar_link_pagamento_cliente só é bindada quando a cobrança do cliente
    # final está de fato habilitada pro tenant — do contrário, a mera presença
    # da tool na lista já muda o comportamento de function-calling do modelo
    # (verificado num teste de integração real: o modelo passou a pedir uma
    # pergunta de esclarecimento antes de transferir mesmo sem a feature
    # habilitada, só por ter uma tool a mais disponível).
    tools_for_agent = [transfer_to_agent, buscar_base_conhecimento_agente, bucar_base_conhecimento_usuario]
    if billing_enabled:
        tools_for_agent.append(gerar_link_pagamento_cliente)
    model_with_tools = model.bind_tools(tools_for_agent)

    prompt = current["instructions"]
    if billing_blocked and is_entry_point:
        packages_text = "\n".join(
            f"- {p['name']}: R$ {p['price_brl']} = {p['credits_granted']} créditos "
            f"(package_id: {p['id']})"
            for p in billing.get("packages", [])
        )
        prompt += (
            "\n\n---\n"
            "**Instrução:** Este cliente está sem créditos disponíveis. Antes de "
            "transferir para outro agente, explique que é necessário comprar "
            "créditos e ofereça os pacotes abaixo. Quando o cliente escolher um, "
            "use a tool gerar_link_pagamento_cliente com o package_id correspondente. "
            "Depois que o cliente confirmar que pagou, chame transfer_to_agent "
            "de novo — é essa chamada que efetivamente libera o outro agente; nunca "
            "diga que já transferiu sem chamar essa ferramenta.\n\n"
            f"Pacotes disponíveis:\n{packages_text}"
        )
    if is_first_run:
        prompt += (
            "\n\n---\n"
            "**Instrução:** Esta é sua primeira resposta neste atendimento. "
            "### Se Apresente, diga sua especialidade e diga que dali para frente é responsável pelo atendimento. "
            "Leia o histórico completo e responda diretamente com seu parecer sobre o caso. "
        )

    response = await model_with_tools.ainvoke([
        SystemMessage(content=prompt),
        *last_messages,
    ])

    update: dict = {"messages": [response], "current_agent_id": current["id"]}
    if is_first_run:
        update["receptive_message_specialist"] = False

    if response.tool_calls:
        tool_name = response.tool_calls[0]["name"]
        logger.info("Ferramenta selecionada | tool={}", tool_name)

        if tool_name == "transfer_to_agent" and not response.content and not billing_blocked:
            target_id = response.tool_calls[0]["args"].get("agent_id")
            target = agents_by_id.get(target_id)
            label = target["name"] if target else "outro agente"
            farewell = f"um momento... vou te passar pra(o) {label} agora."
            response = AIMessage(content=farewell, tool_calls=response.tool_calls, id=response.id)
            update["messages"] = [response]
            logger.info("Despedida de transferência injetada | target={}", target_id)

        return Command(update=update, goto="tool_node")

    logger.info("Modelo respondeu sem chamar ferramentas")
    return Command(update=update, goto=END)


async def tool_node(state: dict) -> dict:
    logger.info("tool_node chamado")

    tools_by_name = {tool.name: tool for tool in tools}
    tool_calls = state["messages"][-1].tool_calls
    logger.info("Processando {} tool call(s)", len(tool_calls))

    agents_by_id = {a["id"]: a for a in state.get("agents", [])}

    messages = []
    state_updates = {}

    for tool_call in tool_calls:
        tool = tools_by_name.get(tool_call["name"])

        if tool is None:
            logger.warning("Ferramenta não encontrada | tool={}", tool_call["name"])
            continue

        args = dict(tool_call["args"])
        if tool_call["name"] in STATE_SCOPED_TOOLS:
            args["conversation_id"] = state["conversation_id"]
        if tool_call["name"] == "buscar_base_conhecimento_agente":
            current = agents_by_id.get(state.get("current_agent_id"))
            args["knowledge_base_file_ids"] = (current or {}).get("knowledge_base_file_ids", [])
        if tool_call["name"] == "transfer_to_agent":
            args["valid_agent_ids"] = list(agents_by_id.keys())
        if tool_call["name"] in BILLING_GATED_TOOLS:
            billing = state.get("end_customer_billing") or {}
            args["end_customer_billing_enabled"] = bool(billing.get("enabled"))
            args["end_customer_balance"] = billing.get("balance", 0)

        logger.info("Executando ferramenta | tool={} | args={}", tool_call["name"], args)
        observation = await tool.ainvoke(args)
        logger.info("Ferramenta concluída | tool={}", tool_call["name"])

        if isinstance(observation, Command):
            if observation.update:
                logger.info("Atualizando estado via Command | updates={}", list(observation.update.keys()))
                state_updates.update(observation.update)
            content = ""
        else:
            content = str(observation)

        messages.append(ToolMessage(content=content, tool_call_id=tool_call["id"]))

    logger.info("tool_node finalizado | mensagens_geradas={}", len(messages))
    return {"messages": messages, **state_updates}
```

- [ ] **Step 6: Substituir o grafo por 2 nós sem arestas condicionais**

Substituir TODO o conteúdo de `apps/agents/agents/workflow.py` por:

```python
from langgraph.graph import StateGraph, START, END
from typing_extensions import Annotated, TypedDict
from langchain.messages import AnyMessage
from agents.nodes import agent_node, tool_node
import operator


class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    attachments: list
    conversation_id: str
    num_before_messages: int
    current_agent_id: str | None
    receptive_message_specialist: bool
    end_customer_billing: dict | None
    agents: list[dict]


graph = StateGraph(State)

graph.add_node("agent_node", agent_node)
graph.add_node("tool_node", tool_node)

graph.add_edge(START, "agent_node")
graph.add_edge("tool_node", "agent_node")
```

(`END` fica importado porque `agent_node`, em `nodes.py`, usa `Command(goto=END)` — não é referenciado diretamente aqui, mas o import de `langgraph.graph` já traz `START`/`END`/`StateGraph` juntos, igual ao arquivo original.)

- [ ] **Step 7: Deletar os 4 arquivos de prompt fixos**

```bash
rm apps/agents/agents/prompts/secretaria.md
rm apps/agents/agents/prompts/condominial.md
rm apps/agents/agents/prompts/contratos.md
rm apps/agents/agents/prompts/direito_consumidor.md
```

(o conteúdo deles já foi clonado para a tabela `agents` na migration `0015` da Etapa 1 e em `default_agents.py` — nenhum código volta a ler esses arquivos depois do Step 5.)

- [ ] **Step 8: Rodar e confirmar sucesso**

Run: `cd apps/agents && uv run pytest tests/unit/test_nodes.py tests/unit/test_helpers.py -v`
Expected: todos passam.

- [ ] **Step 9: Rodar a suite completa de unit (menos integration)**

Run: `cd apps/agents && uv run pytest tests/unit -v`
Expected: todos passam — nenhuma falha residual em `test_tools.py`/`test_retrieval_client.py` (já corrigidos na Task 2) nem em `test_call_agent.py`/`test_helpers.py` (não tocados, testam `sum_usage_breakdown`/`strip_messages`, que não mudaram).

- [ ] **Step 10: Lint**

Run: `cd apps/agents && uv run ruff check agents/nodes.py agents/workflow.py tests/factories.py tests/unit/test_nodes.py`
Expected: `All checks passed!`

- [ ] **Step 11: Commit**

```bash
git add apps/agents/agents/nodes.py apps/agents/agents/workflow.py apps/agents/tests/factories.py apps/agents/tests/unit/test_nodes.py
git rm apps/agents/tests/unit/test_graph_routing.py apps/agents/agents/prompts/secretaria.md apps/agents/agents/prompts/condominial.md apps/agents/agents/prompts/contratos.md apps/agents/agents/prompts/direito_consumidor.md
git commit -m "feat(agents): agent_node genérico substitui os 4 nós fixos do grafo"
```

---

### Task 4: `apps/agents` — thread `agents` end-to-end (`call_agent.py`, `routes.py`, `registry.py`)

**Files:**
- Modify: `apps/agents/services/call_agent.py`
- Modify: `apps/agents/api/routes.py`
- Modify: `apps/agents/agents/registry.py`
- Test: `apps/agents/tests/unit/test_routes.py`

**Interfaces:**
- Consumes: `graph` de `agents.workflow` (Task 3), com o novo campo de estado `agents: list[dict]`.
- Produces: `run_agent(..., agents: list[dict] | None = None) -> tuple[list[str], dict, str | None]` — o 3º item passa a ser o **nome** do agente ativo (ou `None` quando não há resposta). `IncomingMessage.agents: list[dict]` no contrato de `POST /messages` — consumido pela Task 5 (`worker`) e Task 6 (`api`).

- [ ] **Step 1: Escrever o teste que falha para `agents` chegando em `run_agent`**

Em `apps/agents/tests/unit/test_routes.py`, adicionar (depois de `test_sem_end_customer_billing_repassa_none`):

```python
def test_agents_do_payload_e_repassado_ao_run_agent(client, monkeypatch):
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(
        return_value=(["oi"], {"input_tokens": 70, "output_tokens": 30, "total_tokens": 100}, "Secretária")
    )
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    _mock_whatsapp_client(monkeypatch)

    agents_payload = [
        {
            "id": "a1",
            "name": "Secretária",
            "instructions": "Você é a secretária.",
            "is_entry_point": True,
            "knowledge_base_file_ids": [],
        }
    ]
    payload = {**PAYLOAD, "agents": agents_payload}

    response = client.post("/messages", json=payload)

    assert response.status_code == 200
    assert run_agent.call_args.kwargs["agents"] == agents_payload


def test_sem_agents_no_payload_repassa_lista_vazia(client, monkeypatch):
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(
        return_value=(["oi"], {"input_tokens": 70, "output_tokens": 30, "total_tokens": 100}, None)
    )
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    _mock_whatsapp_client(monkeypatch)

    response = client.post("/messages", json=PAYLOAD)

    assert response.status_code == 200
    assert run_agent.call_args.kwargs["agents"] == []
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `cd apps/agents && uv run pytest tests/unit/test_routes.py -v -k agents_do_payload`
Expected: FAIL — `IncomingMessage` não tem campo `agents`, `run_agent` não é chamado com o kwarg `agents`.

- [ ] **Step 3: Atualizar `IncomingMessage` e o call site em `routes.py`**

Em `apps/agents/api/routes.py`, adicionar o campo em `IncomingMessage` (depois de `end_customer_billing`):

```python
class IncomingMessage(BaseModel):
    """Contrato interno: o `api` já resolveu o tenant (via phone_number_id do
    webhook da Meta), validou o estado da conversa (agent|human) e
    descriptografou as credenciais do WhatsApp antes de chamar aqui.

    `send_to_whatsapp=False` (usado pelo playground de admin) roda o grafo
    normalmente mas pula o envio pela Graph API — phone_number_id/access_token
    ficam vazios nesse caso.

    `agents`: a lista completa de agentes do tenant, resolvida pelo chamador
    (worker/api) — nunca lida pelo agents service do Postgres principal.
    """

    tenant_id: str
    contact_phone_number: str
    message: str = ""
    attachments: list = Field(default_factory=list)
    phone_number_id: str = ""
    access_token: str = ""
    send_to_whatsapp: bool = True
    end_customer_billing: dict | None = None
    agents: list[dict] = Field(default_factory=list)
```

E no handler `receive`, no bloco da chamada a `run_agent`, adicionar `agents=body.agents`:

```python
        response, usage, current_agent = await run_agent(
            message=messages["combined_message"],
            attachments=body.attachments,
            conversation_id=thread_id,
            number_whatsapp=body.contact_phone_number,
            end_customer_billing=body.end_customer_billing,
            agents=body.agents,
        )
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/agents && uv run pytest tests/unit/test_routes.py -v`
Expected: todos passam.

- [ ] **Step 5: Escrever o teste que falha para `run_agent` propagar `agents` e resolver o nome**

Não existe teste unitário de `run_agent` hoje (`test_call_agent.py` só testa `sum_usage_breakdown`) porque `run_agent` monta um `AsyncPostgresSaver` real — cobertura fica só pelo teste de rota (Step 1/4, que mocka `run_agent` inteiro) e pelo teste de integração (`tests/integration/test_prompts.py`, que já existe e não muda nesta task). Pule para o Step 6.

- [ ] **Step 6: Implementar a propagação em `call_agent.py`**

Em `apps/agents/services/call_agent.py`, atualizar a assinatura e o corpo de `run_agent`:

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
    agents: list[dict] | None = None,
) -> tuple[list[str], dict, str | None]:
    started_at = time.perf_counter()
    config = {
        "configurable": {"thread_id": conversation_id},
        "callbacks": [langfuse_handler],
    }
    agents = agents or []

    logger.info(
        "Preparando agente | conversation_id={} | num_before_messages={} | has_whatsapp={}",
        conversation_id,
        num_before_messages,
        bool(number_whatsapp),
    )

    async with AsyncPostgresSaver.from_conn_string(db_uri) as checkpointer:
        await checkpointer.setup()
        agent = graph.compile(checkpointer=checkpointer)

        prior_state = await agent.aget_state(config)
        prior_count = (
            len(prior_state.values.get("messages", [])) if prior_state.values else 0
        )

        logger.info("Enviando mensagem ao agente | conversation_id={}", conversation_id)
        response = await agent.ainvoke(
            {
                "messages": [HumanMessage(content=message)],
                "attachments": attachments,
                "conversation_id": conversation_id,
                "num_before_messages": num_before_messages,
                "end_customer_billing": end_customer_billing,
                "agents": agents,
            },
            config=config,
        )

    new_messages = response["messages"][prior_count:]
    answers = [m.content for m in new_messages if m.type == "ai" and m.content]
    usage = sum_usage_breakdown(new_messages)

    agents_by_id = {a["id"]: a for a in agents}
    current_agent_entry = agents_by_id.get(response.get("current_agent_id"))
    current_agent = current_agent_entry["name"] if current_agent_entry else None

    elapsed = round(time.perf_counter() - started_at, 3)
    logger.info(
        "Respostas geradas | conversation_id={} | total={} | tokens={} | current_agent={} | elapsed_s={}",
        conversation_id,
        len(answers),
        usage["total_tokens"],
        current_agent,
        elapsed,
    )
    for i, ans in enumerate(answers):
        logger.debug(
            "Resposta {} | conversation_id={} | content={}", i + 1, conversation_id, ans
        )

    return answers, usage, current_agent
```

- [ ] **Step 7: Rodar e confirmar sucesso**

Run: `cd apps/agents && uv run pytest tests/unit -v`
Expected: todos passam.

- [ ] **Step 8: Revisar `registry.py`**

Substituir TODO o conteúdo de `apps/agents/agents/registry.py` por:

```python
"""Metadados das TOOLS genéricas disponíveis, para o endpoint GET /agents.

Desde a Etapa 2 (agentes por tenant), não existe mais uma lista fixa de
"agentes da plataforma" — cada tenant define os próprios via a tabela
`agents` do `api`. Este registro passou a listar só as tools genéricas do
grafo (o mesmo conjunto para todo tenant), não confundir com essa tabela.
"""

from agents.tools import tools as agent_tools

AGENTS_REGISTRY = {
    "tools": [
        {"name": tool.name, "description": tool.description}
        for tool in agent_tools
    ],
}
```

Não há teste cobrindo `GET /agents` (confirmado: nenhum teste do arquivo `test_routes.py` chama essa rota) — sem passo de TDD aqui, só a revisão do conteúdo.

- [ ] **Step 9: Lint**

Run: `cd apps/agents && uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add apps/agents/services/call_agent.py apps/agents/api/routes.py apps/agents/agents/registry.py apps/agents/tests/unit/test_routes.py
git commit -m "feat(agents): agents list propagado até run_agent, current_agent passa a ser o nome"
```

---

### Task 5: `apps/worker` — carregar e propagar os agentes do tenant

**Files:**
- Modify: `apps/worker/app/tables.py`
- Modify: `apps/worker/app/clients/agents.py`
- Modify: `apps/worker/app/tasks/messages.py`
- Test: `apps/worker/tests/unit/test_agents_client.py`
- Test: `apps/worker/tests/unit/test_load_context.py`
- Test: `apps/worker/tests/unit/test_process_inbound_message.py`

**Interfaces:**
- Consumes: contrato `POST /messages` com `agents: list[dict]` (Task 4).
- Produces: `send_message_to_agents(..., agents: list[dict] | None = None)` — inclui `agents` no payload (lista vazia quando não informado). `InboundContext.agents: list[dict]` — consumido pela chamada a `send_message_to_agents` em `process_inbound_message`.

- [ ] **Step 1: Escrever o teste que falha para `send_message_to_agents` incluir `agents`**

Em `apps/worker/tests/unit/test_agents_client.py`, adicionar (depois de `test_omite_end_customer_billing_quando_none`):

```python
async def test_inclui_agents_quando_informado() -> None:
    response = MagicMock(spec=Response, status_code=200)
    response.json.return_value = {"responses": ["oi"], "tokens_used": 100}
    http = _http_returning(response)
    agents = [
        {
            "id": "a1",
            "name": "Secretária",
            "instructions": "x",
            "is_entry_point": True,
            "knowledge_base_file_ids": [],
        }
    ]

    await send_message_to_agents(http, **KWARGS, agents=agents)

    body = http.post.await_args.kwargs["json"]
    assert body["agents"] == agents


async def test_sem_agents_manda_lista_vazia() -> None:
    response = MagicMock(spec=Response, status_code=200)
    response.json.return_value = {"responses": ["oi"], "tokens_used": 100}
    http = _http_returning(response)

    await send_message_to_agents(http, **KWARGS)

    body = http.post.await_args.kwargs["json"]
    assert body["agents"] == []
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `cd apps/worker && uv run pytest tests/unit/test_agents_client.py -v`
Expected: FAIL em `test_inclui_agents_quando_informado` (`TypeError: send_message_to_agents() got an unexpected keyword argument 'agents'`) e em `test_sem_agents_manda_lista_vazia` (`KeyError: 'agents'`).

- [ ] **Step 3: Implementar `agents` em `send_message_to_agents`**

Em `apps/worker/app/clients/agents.py`, atualizar a assinatura e o corpo:

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
    agents: list[dict] | None = None,
) -> dict | None:
    """Chama POST /messages do agents service.

    Retorna {"responses": [...], "tokens_used": N, "tokens_input": N,
    "tokens_output": N, "delivery_failures": [...]}, ou None quando o agents
    devolve 202 (a mensagem foi agrupada pelo debounce numa execução já em
    andamento — as respostas virão pela execução que está rodando).
    tokens_input/tokens_output valem 0 quando o agents ainda não devolve o
    breakdown (versão antiga durante o deploy).

    `end_customer_billing` (quando não None) leva {"enabled", "balance",
    "packages"} do cliente final — nenhum dado sensível, a secret key da
    Stripe do tenant nunca sai do api.

    `agents`: a lista de agentes do tenant (id, name, instructions,
    is_entry_point, knowledge_base_file_ids) — resolvida aqui a partir do
    Postgres do monorepo antes da chamada; o agents service nunca acessa
    esse banco diretamente.
    """
    headers = {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}
    payload = {
        "tenant_id": tenant_id,
        "contact_phone_number": contact_phone_number,
        "message": message,
        "attachments": [],
        "phone_number_id": phone_number_id,
        "access_token": access_token,
        "agents": agents or [],
    }
    if end_customer_billing is not None:
        # balance vem de end_customer_balances.credit_balance (Numeric(12,4)
        # desde a Etapa 1/2 da wallet unificada) — chega aqui como Decimal,
        # que o encoder JSON padrão não serializa. Converte na fronteira,
        # defensivo contra qualquer chamador (só há um hoje).
        payload["end_customer_billing"] = {
            **end_customer_billing,
            "balance": float(end_customer_billing["balance"]),
        }

    response = await http.post("/messages", json=payload, headers=headers)
    if response.status_code == 202:
        return None
    response.raise_for_status()
    data = response.json()
    return {
        "responses": data.get("responses", []),
        "tokens_used": data.get("tokens_used", 0),
        "tokens_input": data.get("tokens_input", 0),
        "tokens_output": data.get("tokens_output", 0),
        "delivery_failures": data.get("delivery_failures", []),
    }
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/worker && uv run pytest tests/unit/test_agents_client.py -v`
Expected: todos os testes do arquivo passam — os 6 testes pré-existentes continuam OK porque nenhum deles comparava o payload inteiro por igualdade estrita (só chaves específicas via `body["tenant_id"]`/`body["access_token"]`/`body["end_customer_billing"]`, ou a ausência de uma chave).

- [ ] **Step 5: Adicionar as tabelas Core `agents`/`agent_knowledge_base_files`**

Em `apps/worker/app/tables.py`, adicionar (depois da definição de `knowledge_base_files`, mantendo o restante do arquivo intacto):

```python
agents = Table(
    "agents",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("name", String),
    Column("instructions", Text),
    Column("is_entry_point", Boolean),
)

agent_knowledge_base_files = Table(
    "agent_knowledge_base_files",
    metadata,
    Column("agent_id", Uuid, primary_key=True),
    Column("knowledge_base_file_id", Uuid, primary_key=True),
)
```

- [ ] **Step 6: Escrever o teste que falha para `_load_context` carregar os agentes**

Em `apps/worker/tests/unit/test_load_context.py`, o helper `_result` e `_session_with` precisam de duas novas queries no meio da sequência (a lista de agentes e a lista de vínculos com arquivos de KB) — `_load_agents` sempre faz exatamente 2 queries (nunca pula a segunda, mesmo com 0 agentes), então elas entram sempre nas posições 6 e 7, imediatamente depois de `billing_settings` e antes das duas queries condicionais de saldo/pacotes do cliente final. Substituir `_result`/`_session_with` por:

```python
def _session_with(
    conversation,
    content,
    number,
    credit_balance,
    billing_settings,
    balance,
    packages,
    agents_rows=None,
    agent_kb_links=None,
):
    session = AsyncMock()

    def _result(value=None, scalar=None, rows=None):
        result = MagicMock()
        result.one_or_none.return_value = value
        result.scalar_one_or_none.return_value = scalar
        result.scalar_one.return_value = scalar
        result.all.return_value = rows or []
        result.__iter__ = lambda self: iter(rows or [])
        return result

    session.execute = AsyncMock(
        side_effect=[
            _result(value=conversation),
            _result(scalar=content),
            _result(value=number),
            _result(scalar=credit_balance),
            _result(value=billing_settings),
            _result(rows=agents_rows),
            _result(rows=agent_kb_links),
            _result(scalar=balance),
            _result(rows=packages),
        ]
    )
    return session
```

(os 3 testes existentes no arquivo não chamam `_session_with` com `agents_rows`/`agent_kb_links` — ambos ficam `None` → `[]`, e nenhum deles faz asserção sobre `context.agents`, então continuam passando sem qualquer alteração no corpo deles.)

Adicionar, no fim do arquivo, os dois testes novos:

```python
async def test_carrega_agentes_do_tenant_com_arquivos_anexados() -> None:
    agent_id = uuid.uuid4()
    other_agent_id = uuid.uuid4()
    file_id = uuid.uuid4()
    agent_row = SimpleNamespace(
        id=agent_id, name="Secretária", instructions="instruções", is_entry_point=True
    )
    other_row = SimpleNamespace(
        id=other_agent_id,
        name="Condominial",
        instructions="outras instruções",
        is_entry_point=False,
    )
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=None,
        balance=None,
        packages=[],
        agents_rows=[agent_row, other_row],
        agent_kb_links=[(agent_id, file_id)],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.agents == [
        {
            "id": str(agent_id),
            "name": "Secretária",
            "instructions": "instruções",
            "is_entry_point": True,
            "knowledge_base_file_ids": [str(file_id)],
        },
        {
            "id": str(other_agent_id),
            "name": "Condominial",
            "instructions": "outras instruções",
            "is_entry_point": False,
            "knowledge_base_file_ids": [],
        },
    ]


async def test_sem_agentes_retorna_lista_vazia() -> None:
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=None,
        balance=None,
        packages=[],
        agents_rows=[],
        agent_kb_links=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.agents == []
    assert session.execute.await_count == 7
```

- [ ] **Step 7: Rodar e confirmar falha**

Run: `cd apps/worker && uv run pytest tests/unit/test_load_context.py -v`
Expected: FAIL — `AttributeError: 'InboundContext' object has no attribute 'agents'` (o dataclass ainda não tem o campo, então nem chega a rodar a lógica de carregamento).

- [ ] **Step 8: Implementar o carregamento de agentes em `messages.py`**

Em `apps/worker/app/tasks/messages.py`:

1. Adicionar `agents: list[dict]` ao dataclass `InboundContext` (antes do campo com default, `human_last_seen_at`):

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

2. Adicionar a função de carregamento (depois de `_takeover_expirado`, antes de `_sync_context`):

```python
async def _load_agents(session: AsyncSession, tenant_id: str) -> list[dict]:
    """Carrega os agentes do tenant + os ids dos arquivos de KB anexados a
    cada um — nunca lido pelo agents service diretamente do Postgres
    principal, só propagado por aqui em cada POST /messages. Sempre faz as
    duas queries (mesmo com 0 agentes) — o contrato da API garante que todo
    tenant tem ao menos 1 agente, então o caso vazio é só defensivo."""
    agents_result = await session.execute(
        select(
            tables.agents.c.id,
            tables.agents.c.name,
            tables.agents.c.instructions,
            tables.agents.c.is_entry_point,
        ).where(tables.agents.c.tenant_id == uuid.UUID(tenant_id))
    )
    agents_rows = agents_result.all()

    links_result = await session.execute(
        select(
            tables.agent_knowledge_base_files.c.agent_id,
            tables.agent_knowledge_base_files.c.knowledge_base_file_id,
        ).where(
            tables.agent_knowledge_base_files.c.agent_id.in_([row.id for row in agents_rows])
        )
    )
    kb_by_agent: dict[uuid.UUID, list[str]] = {}
    for agent_id, file_id in links_result.all():
        kb_by_agent.setdefault(agent_id, []).append(str(file_id))

    return [
        {
            "id": str(row.id),
            "name": row.name,
            "instructions": row.instructions,
            "is_entry_point": row.is_entry_point,
            "knowledge_base_file_ids": kb_by_agent.get(row.id, []),
        }
        for row in agents_rows
    ]
```

3. Em `_load_context`, chamar `_load_agents` logo depois da query de `billing_settings` (antes do bloco `if end_customer_billing_enabled:`), e incluir o resultado no `InboundContext` retornado no fim da função:

```python
    billing_settings = (
        await session.execute(
            select(tables.tenant_billing_settings.c.enabled).where(
                tables.tenant_billing_settings.c.tenant_id == uuid.UUID(tenant_id)
            )
        )
    ).one_or_none()

    agents = await _load_agents(session, tenant_id)

    end_customer_billing_enabled = bool(billing_settings and billing_settings.enabled)
```

(o resto do corpo de `_load_context` — o bloco `if end_customer_billing_enabled:` com as queries de `balance`/`packages_result` — continua exatamente como está hoje, só depois da linha nova acima.)

E no `return InboundContext(...)` no fim da função, adicionar `agents=agents,`:

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

4. Em `process_inbound_message`, passar `agents=inbound.agents` na chamada a `send_message_to_agents`:

```python
    try:
        result = await send_message_to_agents(
            http,
            tenant_id=tenant_id,
            contact_phone_number=inbound.contact_phone_number,
            message=inbound.message_content,
            phone_number_id=inbound.phone_number_id,
            access_token=access_token,
            agents=inbound.agents,
            **extra_kwargs,
        )
```

- [ ] **Step 9: Rodar e confirmar sucesso de `test_load_context.py`**

Run: `cd apps/worker && uv run pytest tests/unit/test_load_context.py -v`
Expected: todos os 5 testes do arquivo (3 pré-existentes + 2 novos) passam.

- [ ] **Step 10: Corrigir `test_process_inbound_message.py`**

Este arquivo constrói `InboundContext` diretamente via dois helpers (`_inbound` e `_inbound_com_billing`), sem passar por `_load_context` — como `agents` é um campo obrigatório novo (sem default), os dois quebram com `TypeError: __init__() missing 1 required positional argument: 'agents'` em toda chamada. Adicionar `agents=[]` em ambos:

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
        credit_balance=Decimal(credit_balance),
        end_customer_billing_enabled=False,
        end_customer_balance=Decimal(0),
        end_customer_packages=[],
        agents=[],
        human_last_seen_at=human_last_seen_at,
    )
```

```python
def _inbound_com_billing(balance: int, credit_balance: int = 1000) -> InboundContext:
    return InboundContext(
        conversation_state="agent",
        contact_phone_number="5511888888888",
        message_content="Olá",
        phone_number_id="PNID",
        access_token_encrypted="token-cifrado",
        credit_balance=Decimal(credit_balance),
        end_customer_billing_enabled=True,
        end_customer_balance=Decimal(balance),
        end_customer_packages=[
            {"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}
        ],
        agents=[],
    )
```

E adicionar, no fim do arquivo, um teste validando que `agents` chega até `send_message_to_agents`:

```python
async def test_agents_do_inbound_e_repassado_ao_send_message(patched) -> None:
    agents_payload = [
        {
            "id": "a1",
            "name": "Secretária",
            "instructions": "x",
            "is_entry_point": True,
            "knowledge_base_file_ids": [],
        }
    ]
    patched["load"].return_value = InboundContext(
        conversation_state="agent",
        contact_phone_number="5511888888888",
        message_content="Olá",
        phone_number_id="PNID",
        access_token_encrypted="token-cifrado",
        credit_balance=Decimal(1000),
        end_customer_billing_enabled=False,
        end_customer_balance=Decimal(0),
        end_customer_packages=[],
        agents=agents_payload,
    )

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert patched["send"].await_args.kwargs["agents"] == agents_payload
```

- [ ] **Step 11: Rodar e confirmar sucesso da suite completa**

Run: `cd apps/worker && uv run pytest tests/unit -v`
Expected: todos passam — os ~24 testes existentes de `test_process_inbound_message.py` voltam a passar (o `TypeError` do Step 10 desaparece) e o novo teste passa.

- [ ] **Step 12: Lint**

Run: `cd apps/worker && uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 13: Commit**

```bash
git add apps/worker/app/tables.py apps/worker/app/clients/agents.py apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_agents_client.py apps/worker/tests/unit/test_load_context.py apps/worker/tests/unit/test_process_inbound_message.py
git commit -m "feat(worker): carrega e propaga os agentes do tenant pro agents service"
```

---

### Task 6: `apps/api` — carregar e propagar os agentes do tenant (playground + testes)

**Files:**
- Create: `apps/api/app/services/agents_engine.py`
- Modify: `apps/api/app/clients/agents.py`
- Modify: `apps/api/app/services/playground.py`
- Modify: `apps/api/app/services/test_conversations.py`
- Test: `apps/api/tests/unit/test_agents_engine_service.py` (novo)
- Test: `apps/api/tests/unit/test_playground_service.py`
- Test: `apps/api/tests/unit/test_test_conversations_routes.py`

**Interfaces:**
- Consumes: contrato `POST /messages` com `agents: list[dict]` (Task 4).
- Produces: `load_agents_for_engine(session: AsyncSession, tenant_id: uuid.UUID) -> list[dict]` — reusado por `playground.py` e `test_conversations.py`.

- [ ] **Step 1: Escrever o teste que falha para `load_agents_for_engine`**

Criar `apps/api/tests/unit/test_agents_engine_service.py`:

```python
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agents_engine import load_agents_for_engine

TENANT_ID = uuid.uuid4()
AGENT_ID = uuid.uuid4()
OTHER_AGENT_ID = uuid.uuid4()
FILE_ID = uuid.uuid4()


@pytest.fixture
def session():
    return AsyncMock()


async def test_monta_lista_com_arquivos_anexados(session):
    agent_row = SimpleNamespace(
        id=AGENT_ID, name="Secretária", instructions="instruções", is_entry_point=True
    )
    other_row = SimpleNamespace(
        id=OTHER_AGENT_ID, name="Condominial", instructions="outras instruções", is_entry_point=False
    )
    agents_result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [agent_row, other_row]))
    links_result = SimpleNamespace(all=lambda: [(AGENT_ID, FILE_ID)])
    session.execute = AsyncMock(side_effect=[agents_result, links_result])

    result = await load_agents_for_engine(session, TENANT_ID)

    assert result == [
        {
            "id": str(AGENT_ID),
            "name": "Secretária",
            "instructions": "instruções",
            "is_entry_point": True,
            "knowledge_base_file_ids": [str(FILE_ID)],
        },
        {
            "id": str(OTHER_AGENT_ID),
            "name": "Condominial",
            "instructions": "outras instruções",
            "is_entry_point": False,
            "knowledge_base_file_ids": [],
        },
    ]


async def test_sem_agentes_retorna_lista_vazia(session):
    agents_result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
    links_result = SimpleNamespace(all=lambda: [])
    session.execute = AsyncMock(side_effect=[agents_result, links_result])

    result = await load_agents_for_engine(session, TENANT_ID)

    assert result == []
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_engine_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.agents_engine'`.

- [ ] **Step 3: Implementar `load_agents_for_engine`**

Criar `apps/api/app/services/agents_engine.py`:

```python
"""Carrega a lista de agentes de um tenant no formato que o agents service
espera receber em POST /messages — usado pelo playground de admin e pelas
conversas de teste (mensagens reais de WhatsApp usam o equivalente no
worker, ver apps/worker/app/tasks/messages.py). O agents service nunca
acessa este Postgres diretamente."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, AgentKnowledgeBaseFile


async def load_agents_for_engine(session: AsyncSession, tenant_id: uuid.UUID) -> list[dict]:
    agents_result = await session.execute(select(Agent).where(Agent.tenant_id == tenant_id))
    agents = agents_result.scalars().all()

    links_result = await session.execute(
        select(AgentKnowledgeBaseFile.agent_id, AgentKnowledgeBaseFile.knowledge_base_file_id)
        .join(Agent, Agent.id == AgentKnowledgeBaseFile.agent_id)
        .where(Agent.tenant_id == tenant_id)
    )
    kb_by_agent: dict[uuid.UUID, list[str]] = {}
    for agent_id, file_id in links_result.all():
        kb_by_agent.setdefault(agent_id, []).append(str(file_id))

    return [
        {
            "id": str(agent.id),
            "name": agent.name,
            "instructions": agent.instructions,
            "is_entry_point": agent.is_entry_point,
            "knowledge_base_file_ids": kb_by_agent.get(agent.id, []),
        }
        for agent in agents
    ]
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_engine_service.py -v`
Expected: os dois testes passam.

- [ ] **Step 5: Escrever o teste que falha para `send_playground_message` incluir `agents`**

`apps/api/tests/unit/test_agents_client.py` já existe (cobre hoje só `generate_conversation_summary`, via `TestGenerateConversationSummary`) — adicionar uma nova classe no mesmo arquivo, seguindo o mesmo estilo (`httpx.Response` real + `monkeypatch.setattr(httpx.AsyncClient, "post", ...)`, sem mocks de context manager) e o import de `send_playground_message`:

```python
from app.clients.agents import (
    AgentsApiError,
    AgentsNetworkError,
    generate_conversation_summary,
    send_playground_message,
)
```

E adicionar, no fim do arquivo:

```python
class TestSendPlaygroundMessage:
    async def test_inclui_agents_quando_informado(self, monkeypatch) -> None:
        response = httpx.Response(
            200, json={"responses": ["oi"], "tokens_used": 0, "current_agent": None}
        )
        mock_post = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
        agents = [
            {
                "id": "a1",
                "name": "Secretária",
                "instructions": "x",
                "is_entry_point": True,
                "knowledge_base_file_ids": [],
            }
        ]

        await send_playground_message(
            tenant_id="t1", contact_phone_number="playground-s1", message="oi", agents=agents
        )

        body = mock_post.call_args.kwargs["json"]
        assert body["agents"] == agents

    async def test_sem_agents_manda_lista_vazia(self, monkeypatch) -> None:
        response = httpx.Response(
            200, json={"responses": ["oi"], "tokens_used": 0, "current_agent": None}
        )
        mock_post = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        await send_playground_message(
            tenant_id="t1", contact_phone_number="playground-s1", message="oi"
        )

        body = mock_post.call_args.kwargs["json"]
        assert body["agents"] == []
```

- [ ] **Step 6: Rodar e confirmar falha**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_client.py -v`
Expected: FAIL nos dois testes novos — `send_playground_message()` não aceita o kwarg `agents` (`TypeError`).

- [ ] **Step 7: Implementar `agents` em `send_playground_message`**

Em `apps/api/app/clients/agents.py`, atualizar a assinatura e o payload de `send_playground_message`:

```python
async def send_playground_message(
    *, tenant_id: str, contact_phone_number: str, message: str, agents: list[dict] | None = None
) -> dict | None:
    """POST /messages no agents, sem enviar pelo WhatsApp (send_to_whatsapp=False).

    Retorna {"responses": [...], "tokens_used": N, "tokens_input": N,
    "tokens_output": N, "current_agent": "..."}, ou None quando o agents
    devolve 202 (debounce agrupou a mensagem numa execução em andamento —
    as respostas virão pela execução que já roda).

    `agents`: lista de agentes do tenant (id, name, instructions,
    is_entry_point, knowledge_base_file_ids) — ver
    app.services.agents_engine.load_agents_for_engine.
    """
    payload = {
        "tenant_id": tenant_id,
        "contact_phone_number": contact_phone_number,
        "message": message,
        "attachments": [],
        "phone_number_id": "",
        "access_token": "",
        "send_to_whatsapp": False,
        "agents": agents or [],
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.agents_service_url, timeout=_TIMEOUT_SECONDS
        ) as client:
            response = await client.post("/messages", json=payload, headers=_auth_headers())
    except httpx.HTTPError as exc:
        raise AgentsNetworkError(f"Falha de rede ao chamar o agents: {exc}") from exc

    if response.status_code == 202:
        return None
    if response.is_error:
        logger.warning(
            "agents retornou erro no playground | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise AgentsApiError(f"agents HTTP {response.status_code}")

    data = response.json()
    return {
        "responses": data.get("responses", []),
        "tokens_used": data.get("tokens_used"),
        "tokens_input": data.get("tokens_input", 0),
        "tokens_output": data.get("tokens_output", 0),
        "current_agent": data.get("current_agent"),
    }
```

- [ ] **Step 8: Rodar e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_client.py -v`
Expected: os dois testes passam.

- [ ] **Step 9: Escrever o teste que falha para `playground.send_message` carregar e repassar `agents`**

Em `apps/api/tests/unit/test_playground_service.py`, atualizar os testes de `TestSendMessage` que já mockam `send_playground_message` para também mockar `load_agents_for_engine`, e adicionar a asserção do kwarg `agents`. Substituir TODO o conteúdo do arquivo por:

```python
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.clients.agents import AgentsApiError, AgentsNetworkError
from app.services.playground import TenantNotFoundError, delete_conversation, send_message

TENANT_ID = uuid.uuid4()

AGENTS_PAYLOAD = [
    {
        "id": "a1",
        "name": "Secretária",
        "instructions": "x",
        "is_entry_point": True,
        "knowledge_base_file_ids": [],
    }
]


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture(autouse=True)
def load_agents_mock(monkeypatch):
    mock = AsyncMock(return_value=AGENTS_PAYLOAD)
    monkeypatch.setattr("app.services.playground.load_agents_for_engine", mock)
    return mock


class TestSendMessage:
    async def test_tenant_inexistente_levanta_tenant_not_found(self, session, monkeypatch, load_agents_mock):
        session.get.return_value = None
        client_mock = AsyncMock()
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        with pytest.raises(TenantNotFoundError):
            await send_message(session, TENANT_ID, "sess-1", "olá")

        client_mock.assert_not_awaited()
        load_agents_mock.assert_not_awaited()

    async def test_resposta_normal_retorna_dados_do_agente(self, session, monkeypatch, load_agents_mock):
        session.get.return_value = SimpleNamespace(id=TENANT_ID)
        client_mock = AsyncMock(
            return_value={
                "responses": ["oi, como posso ajudar?"],
                "tokens_used": 321,
                "current_agent": "Secretária",
            }
        )
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        result = await send_message(session, TENANT_ID, "sess-1", "olá")

        assert result.responses == ["oi, como posso ajudar?"]
        assert result.tokens_used == 321
        assert result.current_agent == "Secretária"
        assert result.grouped is False
        client_mock.assert_awaited_once_with(
            tenant_id=str(TENANT_ID),
            contact_phone_number="playground-sess-1",
            message="olá",
            agents=AGENTS_PAYLOAD,
        )

    async def test_debounce_agrupou_retorna_grouped_true(self, session, monkeypatch, load_agents_mock):
        session.get.return_value = SimpleNamespace(id=TENANT_ID)
        client_mock = AsyncMock(return_value=None)
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        result = await send_message(session, TENANT_ID, "sess-1", "olá")

        assert result.grouped is True
        assert result.responses == []
        assert result.tokens_used is None
        assert result.current_agent is None

    async def test_erro_do_agents_propaga(self, session, monkeypatch, load_agents_mock):
        session.get.return_value = SimpleNamespace(id=TENANT_ID)
        client_mock = AsyncMock(side_effect=AgentsApiError("HTTP 500"))
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        with pytest.raises(AgentsApiError):
            await send_message(session, TENANT_ID, "sess-1", "olá")

    async def test_erro_de_rede_propaga(self, session, monkeypatch, load_agents_mock):
        session.get.return_value = SimpleNamespace(id=TENANT_ID)
        client_mock = AsyncMock(side_effect=AgentsNetworkError("timeout"))
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        with pytest.raises(AgentsNetworkError):
            await send_message(session, TENANT_ID, "sess-1", "olá")


class TestDeleteConversation:
    async def test_monta_thread_id_com_prefixo_playground(self, monkeypatch):
        delete_mock = AsyncMock()
        monkeypatch.setattr("app.services.playground.delete_agent_checkpoint", delete_mock)

        await delete_conversation(TENANT_ID, "sess-1")

        delete_mock.assert_awaited_once_with(f"{TENANT_ID}:playground-sess-1")
```

- [ ] **Step 10: Rodar e confirmar falha**

Run: `cd apps/api && uv run pytest tests/unit/test_playground_service.py -v`
Expected: FAIL — `app.services.playground.load_agents_for_engine` não existe ainda (o autouse fixture não consegue monkeypatchar um nome inexistente) e `send_message` não chama `send_playground_message` com o kwarg `agents`.

- [ ] **Step 11: Implementar em `playground.py`**

Substituir TODO o conteúdo de `apps/api/app/services/playground.py` por:

```python
"""Envio/limpeza de conversas do playground de agentes (admin) — efêmero:
nada é persistido no Postgres do `api`, a memória vive só no checkpoint do
LangGraph (dentro do agents service)."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.agents import delete_agent_checkpoint, send_playground_message
from app.models import Tenant
from app.schemas.playground import PlaygroundMessageOut
from app.services.agents_engine import load_agents_for_engine


class TenantNotFoundError(Exception):
    pass


async def send_message(
    session: AsyncSession, tenant_id: uuid.UUID, session_id: str, message: str
) -> PlaygroundMessageOut:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise TenantNotFoundError()

    agents = await load_agents_for_engine(session, tenant_id)

    result = await send_playground_message(
        tenant_id=str(tenant_id),
        contact_phone_number=f"playground-{session_id}",
        message=message,
        agents=agents,
    )

    if result is None:
        return PlaygroundMessageOut(
            responses=[], tokens_used=None, current_agent=None, grouped=True
        )

    return PlaygroundMessageOut(
        responses=result["responses"],
        tokens_used=result["tokens_used"],
        current_agent=result["current_agent"],
        grouped=False,
    )


async def delete_conversation(tenant_id: uuid.UUID, session_id: str) -> None:
    await delete_agent_checkpoint(f"{tenant_id}:playground-{session_id}")
```

- [ ] **Step 12: Rodar e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_playground_service.py tests/unit/test_playground_routes.py -v`
Expected: todos passam (`test_playground_routes.py` mocka `send_message` inteiro, então não é afetado por esta mudança interna).

- [ ] **Step 13: Escrever o teste que falha para `test_conversations.send_test_message` carregar e repassar `agents`**

Em `apps/api/tests/unit/test_test_conversations_routes.py`, atualizar a fixture `playground_mock` (dentro de `class TestSendTestMessage`) para também mockar `load_agents_for_engine`:

```python
    @pytest.fixture
    def playground_mock(self, monkeypatch):
        mock = AsyncMock(
            return_value={
                "responses": ["resposta 1", "resposta 2"],
                "tokens_used": 3500,
                "tokens_input": 2800,
                "tokens_output": 700,
                "current_agent": "Secretária",
            }
        )
        monkeypatch.setattr(test_conversations_module.service, "send_playground_message", mock)
        monkeypatch.setattr(
            test_conversations_module.service,
            "load_agents_for_engine",
            AsyncMock(return_value=[]),
        )
        pricing = SimpleNamespace(
            id=uuid.uuid4(),
            tokens_per_credit=1000,
            input_weight=Decimal("0.3"),
            output_weight=Decimal("1.0"),
        )
        monkeypatch.setattr(
            test_conversations_module.service,
            "get_current_pricing_config",
            AsyncMock(return_value=pricing),
        )
        return mock
```

E adicionar, na mesma classe, um novo teste:

```python
    def test_agents_do_tenant_e_repassado_ao_send_playground_message(
        self, client, session, playground_mock, monkeypatch
    ) -> None:
        self._arm_session(session, _conversation())
        agents_payload = [
            {
                "id": "a1",
                "name": "Secretária",
                "instructions": "x",
                "is_entry_point": True,
                "knowledge_base_file_ids": [],
            }
        ]
        monkeypatch.setattr(
            test_conversations_module.service,
            "load_agents_for_engine",
            AsyncMock(return_value=agents_payload),
        )

        client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            json={"content": "oi"},
        )

        assert playground_mock.await_args.kwargs["agents"] == agents_payload
```

- [ ] **Step 14: Rodar e confirmar falha**

Run: `cd apps/api && uv run pytest tests/unit/test_test_conversations_routes.py -v`
Expected: FAIL — a fixture `playground_mock` já tenta monkeypatchar `load_agents_for_engine` em `test_conversations_module.service`, que ainda não existe (`AttributeError`).

- [ ] **Step 15: Implementar em `test_conversations.py`**

Em `apps/api/app/services/test_conversations.py`, adicionar o import:

```python
from app.services.agents_engine import load_agents_for_engine
```

E, em `send_test_message`, carregar os agentes antes de chamar `send_playground_message` e repassá-los:

```python
    result = await send_playground_message(
        tenant_id=str(tenant_id),
        contact_phone_number=conversation.contact_phone_number,
        message=content,
        agents=await load_agents_for_engine(session, tenant_id),
    )
```

(essa é a única mudança no corpo da função — o resto de `send_test_message` continua exatamente igual.)

- [ ] **Step 16: Rodar e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_test_conversations_routes.py -v`
Expected: todos passam.

- [ ] **Step 17: Rodar a suite completa do `api`**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v`
Expected: todos passam ou skip (os mesmos skips pré-existentes de integração que exigem Postgres real).

- [ ] **Step 18: Lint**

Run: `cd apps/api && uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 19: Commit**

```bash
git add apps/api/app/services/agents_engine.py apps/api/app/clients/agents.py apps/api/app/services/playground.py apps/api/app/services/test_conversations.py apps/api/tests/unit/test_agents_engine_service.py apps/api/tests/unit/test_agents_client.py apps/api/tests/unit/test_playground_service.py apps/api/tests/unit/test_test_conversations_routes.py
git commit -m "feat(api): carrega e propaga os agentes do tenant no playground e nas conversas de teste"
```

---

### Task 7: Documentação — `API_AGENTS.md` e `CLAUDE.md`

**Files:**
- Modify: `apps/agents/API_AGENTS.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: comportamento final das Tasks 1-6 (deve ser a última task).
- Produces: nenhum código — só documentação, sem passo de teste.

- [ ] **Step 1: Atualizar a seção 3.1 de `API_AGENTS.md` (contrato de `POST /messages`)**

Localizar a seção `### 3.1 \`POST /messages\` — Recebimento de mensagens (contrato interno)` e adicionar, na descrição do corpo da requisição e da resposta, os dois campos novos:
- No corpo: `agents` (lista de objetos `{id, name, instructions, is_entry_point, knowledge_base_file_ids}` — a lista completa de agentes do tenant, resolvida pelo chamador; omitido/vazio quando o tenant não tem agentes carregados, o que faz o grafo devolver uma mensagem de erro genérica).
- Na resposta: `current_agent` deixou de ser a chave interna de um nó fixo (`"agente_secretaria"`) e passou a ser o **nome** do agente que respondeu por último (ex: `"Secretária"`, ou o nome customizado que o tenant deu ao agente).

- [ ] **Step 2: Atualizar a seção 5 (`O grafo de agentes`)**

Reescrever os itens 5.1 (`Estado`), 5.2 (`Nós`) e 5.3 (`Roteamento`) para descrever o grafo de 2 nós (`agent_node`, `tool_node`, arestas fixas `START → agent_node`, `tool_node → agent_node`, sem arestas condicionais) e o campo de estado `current_agent_id`/`agents`, removendo as referências aos 4 nós fixos antigos (`agente_secretaria`/`agente_condominial`/`agente_contratos`/`agente_direito_consumidor`) e às funções de roteamento `route_from_start`/`route_from_tool_node` (que não existem mais).

- [ ] **Step 3: Atualizar a seção 6 (`Ferramentas`)**

Substituir as referências a `transfer_to_specialist` por `transfer_to_agent` (incluindo a validação de `agent_id` contra `valid_agent_ids`) e às 3 tools fixas de categoria + `buscar_base_conhecimento_escritorio` por `buscar_base_conhecimento_agente` (escopada pelos `knowledge_base_file_ids` do agente ativo, injetados pelo `tool_node`).

- [ ] **Step 4: Atualizar a seção 11 (`Débitos técnicos / atenção`)**

Remover a linha sobre "despedida de transferência automática só implementada para secretária/condominial" (fechada por esta etapa — agora se aplica a qualquer agente). Manter as demais linhas dessa seção (URL/API_KEY hardcoded em `enviar_documento`, tools de geração de documento não implementadas) intactas — fora de escopo desta etapa.

- [ ] **Step 5: Atualizar `CLAUDE.md`**

Na seção "Agents Service (`apps/agents`)":
- Trocar o parágrafo "Grafo composto por uma secretária de triagem... a partir daí a conversa fica fixada nesse especialista" por uma descrição do motor genérico: grafo de 2 nós (`agent_node`/`tool_node`), agente ativo resolvido de `state["agents"]` por `current_agent_id` (fallback pro agente com `is_entry_point=True`), tools genéricas `transfer_to_agent`/`buscar_base_conhecimento_agente` validadas/injetadas pelo `tool_node`.
- Remover a linha de pendência "Avaliar se os 3 especialistas hardcoded do `agents` ... são o conjunto fixo de agentes de toda a plataforma ou precisam generalizar" da seção "Retrofit de `apps/agents` e `apps/api_rag` para multi-tenancy" — resolvida por esta etapa (marcar como `[x]` com nota "feito — cada tenant define os próprios agentes, ver Etapa 2 do plano de agentes por tenant").
- Na frase inicial desta feature no topo do arquivo (parágrafo de estado atual do `apps/api`/`apps/agents`), acrescentar a menção de que o motor de agentes do `apps/agents` agora é dinâmico por tenant (sem mudar o resto da frase).

- [ ] **Step 6: Commit**

```bash
git add apps/agents/API_AGENTS.md CLAUDE.md
git commit -m "docs: atualiza API_AGENTS.md e CLAUDE.md pro motor dinâmico de agentes"
```
