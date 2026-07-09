# Playground de Agentes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chat de teste em `/admin/playground` para desenvolvedores conversarem com os agentes de qualquer tenant sem passar pelo WhatsApp, vendo qual agente (secretária ou especialista) está atendendo a conversa.

**Architecture:** O `api` expõe rotas novas (`platform-admin/playground/*`, autenticadas com `get_current_platform_admin`) que chamam o `agents` service síncronamente via um client HTTP novo, repassando um flag `send_to_whatsapp=false` que faz o `agents` pular o envio pela Graph API mas rodar o grafo normalmente. Nada é persistido no Postgres do `api` — a memória da conversa vive só no checkpoint do LangGraph, isolada por um `thread_id` com prefixo `playground-`.

**Tech Stack:** FastAPI + httpx (api, agents), Next.js 15 App Router + React (web).

## Global Constraints

- **Efêmero por design**: nenhuma escrita em `conversations`/`messages`/`credit_transactions`/`tenants.credit_balance` a partir do playground.
- **Debounce mantido**: o `agents` continua agrupando mensagens em rajada (~5s) mesmo com `send_to_whatsapp=false` — comportamento idêntico ao real.
- **Isolamento de thread**: `contact_phone_number = "playground-{session_id}"` → `thread_id = "{tenant_id}:playground-{session_id}"`, nunca colide com um contato real (contatos reais são números de telefone, nunca começam com `"playground-"`).
- **Compatibilidade retroativa**: `send_to_whatsapp` tem default `true` e `phone_number_id`/`access_token` continuam funcionando exatamente como hoje para o `worker` — nenhuma mudança no contrato que o `worker` já usa.
- **Autenticação**: toda rota nova do `api` exige `Depends(get_current_platform_admin)` — mesmo isolamento de sessão do resto do `/admin`.
- **Só texto** nesta entrega — sem upload de anexo no playground.
- Mensagens/comentários em pt-BR com acentuação correta.
- Comandos: `apps/agents` e `apps/api` → `uv run pytest tests/unit`, `uv run ruff check .`, `uv run ruff format --check .`. `apps/web` → `pnpm test`, `pnpm lint`, `pnpm build` (via `npx --yes pnpm@9 <comando>` se `pnpm` não estiver disponível globalmente).

---

### Task 1: `agents` — flag `send_to_whatsapp` e `current_agent` no retorno

**Files:**
- Modify: `apps/agents/services/call_agent.py`
- Modify: `apps/agents/api/routes.py`
- Modify: `apps/agents/tests/unit/test_routes.py`
- Modify: `apps/agents/API_AGENTS.md`

**Interfaces:**
- Produces: `run_agent(...) -> tuple[list[str], int, str]` (terceiro elemento = `current_agent`, ex: `"agente_secretaria"`); `IncomingMessage.send_to_whatsapp: bool = True`; `IncomingMessage.phone_number_id`/`access_token` agora com default `""`; resposta de `POST /messages` ganha a chave `"current_agent"`.

- [ ] **Step 1: Escrever o teste que falha (rota — `send_to_whatsapp=False` não envia WhatsApp)**

Em `apps/agents/tests/unit/test_routes.py`, adicionar (após `test_fluxo_feliz_envia_respostas_e_retorna_lista`):

```python
def test_send_to_whatsapp_false_nao_envia_mas_retorna_respostas(client, monkeypatch):
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(
        return_value=(["resposta 1", "resposta 2"], 1234, "agente_condominial")
    )
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    wa_cls, wa_instance = _mock_whatsapp_client(monkeypatch)

    payload = {**PAYLOAD, "phone_number_id": "", "access_token": "", "send_to_whatsapp": False}
    response = client.post("/messages", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "responses": ["resposta 1", "resposta 2"],
        "tokens_used": 1234,
        "current_agent": "agente_condominial",
    }
    wa_cls.assert_not_called()
    wa_instance.send_text_message.assert_not_awaited()


def test_send_to_whatsapp_default_true_continua_enviando(client, monkeypatch):
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(return_value=(["resposta 1"], 100, None))
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    wa_cls, wa_instance = _mock_whatsapp_client(monkeypatch)

    response = client.post("/messages", json=PAYLOAD)

    assert response.status_code == 200
    assert response.json()["current_agent"] is None
    wa_cls.assert_called_once_with("111222333", "token-do-tenant")
    wa_instance.send_text_message.assert_awaited_once_with("5511999999999", "resposta 1")
```

Também atualizar `test_fluxo_feliz_envia_respostas_e_retorna_lista` (já existente) — trocar o mock de `run_agent` para o novo formato de 3-tupla e ajustar a asserção do corpo da resposta:

```python
def test_fluxo_feliz_envia_respostas_e_retorna_lista(client, monkeypatch):
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(return_value=(["resposta 1", "resposta 2"], 1234, "agente_secretaria"))
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    wa_cls, wa_instance = _mock_whatsapp_client(monkeypatch)

    response = client.post("/messages", json=PAYLOAD)

    assert response.status_code == 200
    assert response.json() == {
        "responses": ["resposta 1", "resposta 2"],
        "tokens_used": 1234,
        "current_agent": "agente_secretaria",
    }

    # thread_id composto por tenant + telefone do contato
    expected_thread = "tenant-1:5511999999999"
    assert debounce.call_args.kwargs["conversation_id"] == expected_thread
    assert run_agent.call_args.kwargs["conversation_id"] == expected_thread

    # cliente WhatsApp criado com as credenciais do tenant e chamado por resposta
    wa_cls.assert_called_once_with("111222333", "token-do-tenant")
    assert wa_instance.send_text_message.await_count == 2
    wa_instance.send_text_message.assert_awaited_with("5511999999999", "resposta 2")
```

- [ ] **Step 2: Rodar os testes e ver falhar**

Run: `cd apps/agents && uv run pytest tests/unit/test_routes.py -v`
Expected: FAIL — `test_fluxo_feliz_envia_respostas_e_retorna_lista` (chave `current_agent` ausente do JSON), `test_send_to_whatsapp_false_nao_envia_mas_retorna_respostas` e `test_send_to_whatsapp_default_true_continua_enviando` (campo `send_to_whatsapp` não existe em `IncomingMessage` → `422`).

- [ ] **Step 3: `run_agent` devolve o agente ativo**

Em `apps/agents/services/call_agent.py`, trocar a assinatura e o final da função:

```python
async def run_agent(
    message: str,
    conversation_id: str,
    attachments: list = [],
    number_whatsapp: str | None = None,
    db_uri: str = DB_URI,
    num_before_messages: int = 35,
    extra_data: dict = {},
) -> tuple[list[str], int, str]:
```

E, no final da função (onde hoje está `return answers, tokens_used`), trocar por:

```python
    current_agent = response.get("current_specialist") or "agente_secretaria"

    elapsed = round(time.perf_counter() - started_at, 3)
    logger.info(
        "Respostas geradas | conversation_id={} | total={} | tokens={} | current_agent={} | elapsed_s={}",
        conversation_id,
        len(answers),
        tokens_used,
        current_agent,
        elapsed,
    )
    for i, ans in enumerate(answers):
        logger.debug("Resposta {} | conversation_id={} | content={}", i + 1, conversation_id, ans)

    return answers, tokens_used, current_agent
```

(Remova o bloco de log antigo que ficava antes desse — este substitui, não duplica; o `elapsed`/logger.info original é reaproveitado com o campo `current_agent` adicionado.)

- [ ] **Step 4: `IncomingMessage` ganha `send_to_whatsapp` e credenciais opcionais; rota pula o envio**

Em `apps/agents/api/routes.py`:

```python
class IncomingMessage(BaseModel):
    """Contrato interno: o `api` já resolveu o tenant (via phone_number_id do
    webhook da Meta), validou o estado da conversa (agent|human) e
    descriptografou as credenciais do WhatsApp antes de chamar aqui.

    `send_to_whatsapp=False` (usado pelo playground de admin) roda o grafo
    normalmente mas pula o envio pela Graph API — phone_number_id/access_token
    ficam vazios nesse caso.
    """

    tenant_id: str
    contact_phone_number: str
    message: str = ""
    attachments: list = Field(default_factory=list)
    phone_number_id: str = ""
    access_token: str = ""
    send_to_whatsapp: bool = True
```

E o bloco final de `receive`:

```python
    try:
        logger.info("Encaminhando mensagem ao agente | thread_id={}", thread_id)
        response, tokens_used, current_agent = await run_agent(
            message=messages["combined_message"],
            attachments=body.attachments,
            conversation_id=thread_id,
            number_whatsapp=body.contact_phone_number,
        )

        if body.send_to_whatsapp:
            logger.info(
                "Enviando {} resposta(s) via WhatsApp | thread_id={}",
                len(response),
                thread_id,
            )
            async with WhatsAppClient(body.phone_number_id, body.access_token) as client:
                for msg in response:
                    await client.send_text_message(body.contact_phone_number, msg)
        else:
            logger.info("send_to_whatsapp=False — envio pulado | thread_id={}", thread_id)

        # Devolve as respostas e os tokens da execução para o chamador
        # (`worker`) persistir em `messages` e debitar os créditos.
        return {"responses": response, "tokens_used": tokens_used, "current_agent": current_agent}
    except Exception:
        logger.exception("Erro ao chamar o agente | thread_id={}", thread_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro interno ao processar resposta do agente.",
        )
```

- [ ] **Step 5: Rodar os testes e ver passar**

Run: `cd apps/agents && uv run pytest tests/unit -v`
Expected: PASS em todos os testes, incluindo os 3 tocados/criados neste Step.

- [ ] **Step 6: Lint**

Run: `cd apps/agents && uv run ruff check . && uv run ruff format --check .`
Expected: sem erros.

- [ ] **Step 7: Atualizar `API_AGENTS.md`**

Na seção `### 3.1 POST /messages`, trocar o bloco de corpo esperado por:

```jsonc
{
  "tenant_id": "uuid-do-escritorio",           // obrigatório
  "contact_phone_number": "5511999999999",     // obrigatório; cliente final
  "message": "texto da mensagem do cliente",   // opcional se houver attachments
  "attachments": [],                           // opcional
  "phone_number_id": "1234567890",             // obrigatório quando send_to_whatsapp=true (default)
  "access_token": "EAAG...",                   // obrigatório quando send_to_whatsapp=true (default)
  "send_to_whatsapp": true                     // opcional, default true — false pula o envio via Graph API (usado pelo playground de admin em apps/api)
}
```

E o bloco de resposta de sucesso por:

```json
{ "responses": ["resposta 1", "resposta 2"], "tokens_used": 1234, "current_agent": "agente_condominial" }
```

Acrescentar, logo após o parágrafo que já explica `tokens_used`:

> `current_agent` é o nome interno do agente que respondeu por último nesta execução (`"agente_secretaria"` ou um dos 3 especialistas) — lido do estado do grafo (`current_specialist`, `None` antes de qualquer transferência). Usado hoje só pelo playground de admin para exibir uma tag do agente ativo na conversa.

E, no fluxo interno (item 4, "Envio"), trocar por:

```
4. **Envio** (só quando `send_to_whatsapp=true`, o default) — cada resposta
   gerada é enviada ao cliente via `WhatsAppClient.send_text_message` (Graph
   API), usando as credenciais do tenant recebidas na request. Com
   `send_to_whatsapp=false` este passo é pulado — usado pelo playground de
   admin (`apps/api`), que só quer as respostas de volta, sem canal.
```

- [ ] **Step 8: Commit**

```bash
git add apps/agents/services/call_agent.py apps/agents/api/routes.py apps/agents/tests/unit/test_routes.py apps/agents/API_AGENTS.md
git commit -m "feat(agents): flag send_to_whatsapp e current_agent no retorno de POST /messages"
```

---

### Task 2: `api` — client, service e rotas do playground

**Files:**
- Modify: `apps/api/app/core/config.py`
- Create: `apps/api/app/clients/agents.py`
- Create: `apps/api/app/schemas/playground.py`
- Create: `apps/api/app/services/playground.py`
- Create: `apps/api/app/api/v1/platform_admin/playground.py`
- Modify: `apps/api/app/api/v1/router.py`
- Test: `apps/api/tests/unit/test_playground_service.py`
- Test: `apps/api/tests/unit/test_playground_routes.py`

**Interfaces:**
- Consumes: `get_current_platform_admin`/`PlatformAdminContext` (`app/api/deps.py`, já existente); `Tenant` model (`app/models`, já existente).
- Produces: `send_playground_message(*, tenant_id, contact_phone_number, message) -> dict | None`, `delete_playground_conversation(thread_id) -> None`, exceções `AgentsNetworkError`/`AgentsApiError` em `app.clients.agents`; `send_message(session, tenant_id, session_id, message) -> PlaygroundMessageOut`, `delete_conversation(tenant_id, session_id) -> None`, exceção `TenantNotFoundError` em `app.services.playground`; `POST /api/v1/platform-admin/playground/messages`, `DELETE /api/v1/platform-admin/playground/conversations/{tenant_id}/{session_id}`.

- [ ] **Step 1: Config — `agents_api_key`**

Em `apps/api/app/core/config.py`, adicionar (a env `AGENTS_API_KEY` já existe no `.env`/`.env.example`, compartilhada com o `worker`):

```python
    agents_service_url: str = "http://agents:8001"
    # Auth de serviço com o agents (playground de admin — o worker usa a
    # mesma env, mas cada serviço lê o próprio settings).
    agents_api_key: str = ""
```

(troque a linha `agents_service_url: str = "http://agents:8001"` já existente por essas duas linhas, no mesmo lugar.)

- [ ] **Step 2: Escrever o teste que falha (client)**

Criar `apps/api/tests/unit/test_playground_service.py`:

```python
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.clients.agents import AgentsApiError, AgentsNetworkError
from app.services.playground import TenantNotFoundError, delete_conversation, send_message

TENANT_ID = uuid.uuid4()


@pytest.fixture
def session():
    return AsyncMock()


class TestSendMessage:
    async def test_tenant_inexistente_levanta_tenant_not_found(self, session, monkeypatch):
        session.get.return_value = None
        client_mock = AsyncMock()
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        with pytest.raises(TenantNotFoundError):
            await send_message(session, TENANT_ID, "sess-1", "olá")

        client_mock.assert_not_awaited()

    async def test_resposta_normal_retorna_dados_do_agente(self, session, monkeypatch):
        session.get.return_value = SimpleNamespace(id=TENANT_ID)
        client_mock = AsyncMock(
            return_value={
                "responses": ["oi, como posso ajudar?"],
                "tokens_used": 321,
                "current_agent": "agente_secretaria",
            }
        )
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        result = await send_message(session, TENANT_ID, "sess-1", "olá")

        assert result.responses == ["oi, como posso ajudar?"]
        assert result.tokens_used == 321
        assert result.current_agent == "agente_secretaria"
        assert result.grouped is False
        client_mock.assert_awaited_once_with(
            tenant_id=str(TENANT_ID),
            contact_phone_number="playground-sess-1",
            message="olá",
        )

    async def test_debounce_agrupou_retorna_grouped_true(self, session, monkeypatch):
        session.get.return_value = SimpleNamespace(id=TENANT_ID)
        client_mock = AsyncMock(return_value=None)
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        result = await send_message(session, TENANT_ID, "sess-1", "olá")

        assert result.grouped is True
        assert result.responses == []
        assert result.tokens_used is None
        assert result.current_agent is None

    async def test_erro_do_agents_propaga(self, session, monkeypatch):
        session.get.return_value = SimpleNamespace(id=TENANT_ID)
        client_mock = AsyncMock(side_effect=AgentsApiError("HTTP 500"))
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        with pytest.raises(AgentsApiError):
            await send_message(session, TENANT_ID, "sess-1", "olá")

    async def test_erro_de_rede_propaga(self, session, monkeypatch):
        session.get.return_value = SimpleNamespace(id=TENANT_ID)
        client_mock = AsyncMock(side_effect=AgentsNetworkError("timeout"))
        monkeypatch.setattr("app.services.playground.send_playground_message", client_mock)

        with pytest.raises(AgentsNetworkError):
            await send_message(session, TENANT_ID, "sess-1", "olá")


class TestDeleteConversation:
    async def test_monta_thread_id_com_prefixo_playground(self, monkeypatch):
        delete_mock = AsyncMock()
        monkeypatch.setattr("app.services.playground.delete_playground_conversation", delete_mock)

        await delete_conversation(TENANT_ID, "sess-1")

        delete_mock.assert_awaited_once_with(f"{TENANT_ID}:playground-sess-1")
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_playground_service.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'app.clients.agents'` (ou `app.services.playground`).

- [ ] **Step 4: Client HTTP do `agents`**

Criar `apps/api/app/clients/agents.py`:

```python
"""Client HTTP para o agents service — usado hoje só pelo playground do
admin (mensagens reais de WhatsApp são enviadas pelo `worker`, não pelo `api`)."""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 120
_DELETE_TIMEOUT_SECONDS = 15


class AgentsNetworkError(Exception):
    """Falha de rede ao chamar o agents service (timeout, conexão, DNS)."""


class AgentsApiError(Exception):
    """O agents service respondeu com erro (não-2xx, exceto 202)."""


def _auth_headers() -> dict[str, str]:
    return {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}


async def send_playground_message(
    *, tenant_id: str, contact_phone_number: str, message: str
) -> dict | None:
    """POST /messages no agents, sem enviar pelo WhatsApp (send_to_whatsapp=False).

    Retorna {"responses": [...], "tokens_used": N, "current_agent": "..."},
    ou None quando o agents devolve 202 (debounce agrupou a mensagem numa
    execução em andamento — as respostas virão pela execução que já roda).
    """
    payload = {
        "tenant_id": tenant_id,
        "contact_phone_number": contact_phone_number,
        "message": message,
        "attachments": [],
        "phone_number_id": "",
        "access_token": "",
        "send_to_whatsapp": False,
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
        "current_agent": data.get("current_agent"),
    }


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

- [ ] **Step 5: Schemas**

Criar `apps/api/app/schemas/playground.py`:

```python
import uuid

from pydantic import BaseModel, Field


class PlaygroundMessageRequest(BaseModel):
    tenant_id: uuid.UUID
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1)


class PlaygroundMessageOut(BaseModel):
    responses: list[str]
    tokens_used: int | None
    current_agent: str | None
    grouped: bool
```

- [ ] **Step 6: Service**

Criar `apps/api/app/services/playground.py`:

```python
"""Envio/limpeza de conversas do playground de agentes (admin) — efêmero:
nada é persistido no Postgres do `api`, a memória vive só no checkpoint do
LangGraph (dentro do agents service)."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.agents import delete_playground_conversation, send_playground_message
from app.models import Tenant
from app.schemas.playground import PlaygroundMessageOut


class TenantNotFoundError(Exception):
    pass


async def send_message(
    session: AsyncSession, tenant_id: uuid.UUID, session_id: str, message: str
) -> PlaygroundMessageOut:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise TenantNotFoundError()

    result = await send_playground_message(
        tenant_id=str(tenant_id),
        contact_phone_number=f"playground-{session_id}",
        message=message,
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
    await delete_playground_conversation(f"{tenant_id}:playground-{session_id}")
```

- [ ] **Step 7: Rodar e ver passar (service)**

Run: `cd apps/api && uv run pytest tests/unit/test_playground_service.py -v`
Expected: PASS (7/7).

- [ ] **Step 8: Escrever o teste que falha (rotas)**

Criar `apps/api/tests/unit/test_playground_routes.py`:

```python
import uuid
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import app.api.v1.platform_admin.playground as playground_module
from app.api.deps import PlatformAdminContext, get_current_platform_admin
from app.clients.agents import AgentsApiError, AgentsNetworkError
from app.core.db import get_session
from app.main import app
from app.schemas.playground import PlaygroundMessageOut
from app.services.playground import TenantNotFoundError

TENANT_ID = uuid.uuid4()
BODY = {"tenant_id": str(TENANT_ID), "session_id": "sess-1", "message": "olá"}


def _client():
    async def override_admin():
        return PlatformAdminContext(admin_id=uuid.uuid4(), role="superadmin")

    async def override_session():
        yield AsyncMock()

    app.dependency_overrides[get_current_platform_admin] = override_admin
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


class TestSendMessageRoute:
    def test_sem_token_retorna_401(self):
        response = TestClient(app).post("/api/v1/platform-admin/playground/messages", json=BODY)
        assert response.status_code == 401

    def test_sucesso_retorna_200(self, monkeypatch):
        monkeypatch.setattr(
            playground_module,
            "send_message",
            AsyncMock(
                return_value=PlaygroundMessageOut(
                    responses=["oi!"], tokens_used=100, current_agent="agente_secretaria", grouped=False
                )
            ),
        )
        client = _client()
        try:
            response = client.post("/api/v1/platform-admin/playground/messages", json=BODY)
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json()["responses"] == ["oi!"]

    def test_tenant_inexistente_retorna_404(self, monkeypatch):
        monkeypatch.setattr(
            playground_module, "send_message", AsyncMock(side_effect=TenantNotFoundError())
        )
        client = _client()
        try:
            response = client.post("/api/v1/platform-admin/playground/messages", json=BODY)
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    def test_erro_do_agents_retorna_502(self, monkeypatch):
        monkeypatch.setattr(
            playground_module, "send_message", AsyncMock(side_effect=AgentsApiError("HTTP 500"))
        )
        client = _client()
        try:
            response = client.post("/api/v1/platform-admin/playground/messages", json=BODY)
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 502

    def test_erro_de_rede_retorna_502(self, monkeypatch):
        monkeypatch.setattr(
            playground_module, "send_message", AsyncMock(side_effect=AgentsNetworkError("timeout"))
        )
        client = _client()
        try:
            response = client.post("/api/v1/platform-admin/playground/messages", json=BODY)
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 502

    def test_mensagem_vazia_retorna_422(self):
        client = _client()
        try:
            response = client.post(
                "/api/v1/platform-admin/playground/messages",
                json={**BODY, "message": ""},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422


class TestDeleteConversationRoute:
    def test_sem_token_retorna_401(self):
        response = TestClient(app).delete(
            f"/api/v1/platform-admin/playground/conversations/{TENANT_ID}/sess-1"
        )
        assert response.status_code == 401

    def test_sucesso_retorna_204(self, monkeypatch):
        monkeypatch.setattr(playground_module, "delete_conversation", AsyncMock())
        client = _client()
        try:
            response = client.delete(
                f"/api/v1/platform-admin/playground/conversations/{TENANT_ID}/sess-1"
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 204
```

- [ ] **Step 9: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_playground_routes.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'app.api.v1.platform_admin.playground'`.

- [ ] **Step 10: Rotas**

Criar `apps/api/app/api/v1/platform_admin/playground.py`:

```python
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PlatformAdminContext, get_current_platform_admin
from app.clients.agents import AgentsApiError, AgentsNetworkError
from app.core.db import get_session
from app.schemas.playground import PlaygroundMessageOut, PlaygroundMessageRequest
from app.services.playground import TenantNotFoundError, delete_conversation, send_message

router = APIRouter(prefix="/platform-admin/playground", tags=["platform-admin"])

_AGENTS_ERROR_DETAIL = "Não foi possível falar com o agente agora."


@router.post("/messages")
async def send_playground_message_route(
    body: PlaygroundMessageRequest,
    admin: PlatformAdminContext = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> PlaygroundMessageOut:
    try:
        return await send_message(session, body.tenant_id, body.session_id, body.message)
    except TenantNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant não encontrado")
    except (AgentsNetworkError, AgentsApiError):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_AGENTS_ERROR_DETAIL)


@router.delete(
    "/conversations/{tenant_id}/{session_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_playground_conversation_route(
    tenant_id: uuid.UUID,
    session_id: str,
    admin: PlatformAdminContext = Depends(get_current_platform_admin),
) -> None:
    await delete_conversation(tenant_id, session_id)
```

- [ ] **Step 11: Registrar no router principal**

Em `apps/api/app/api/v1/router.py`, adicionar:

```python
from app.api.v1.platform_admin.playground import router as platform_admin_playground_router
```

```python
api_router.include_router(platform_admin_playground_router)
```

- [ ] **Step 12: Rodar todos os testes e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos PASS, ruff limpo.

- [ ] **Step 13: Commit**

```bash
git add apps/api/app/core/config.py apps/api/app/clients/agents.py apps/api/app/schemas/playground.py apps/api/app/services/playground.py apps/api/app/api/v1/platform_admin/playground.py apps/api/app/api/v1/router.py apps/api/tests/unit/test_playground_service.py apps/api/tests/unit/test_playground_routes.py
git commit -m "feat(api): rotas do playground de agentes no painel de administração"
```

---

### Task 3: `web` — extrair `AdminNav` compartilhado

**Files:**
- Create: `apps/web/src/components/AdminNav.tsx`
- Modify: `apps/web/src/app/admin/page.tsx`
- Modify: `apps/web/src/app/admin/tenants/page.tsx`
- Modify: `apps/web/src/app/admin/tenants/[id]/page.tsx`
- Test: `apps/web/__tests__/AdminNav.test.tsx`

**Interfaces:**
- Consumes: `adminLogout` (`@/app/admin/actions`, já existente).
- Produces: `AdminNav({ active: "dashboard" | "tenants" | "playground" })` em `@/components/AdminNav`.

**Contexto**: as 3 páginas do admin hoje duplicam o mesmo bloco de `<nav>` (só o item "atual" muda de `<Link>` pra `<span>`). Esta task extrai isso pra um componente único — necessário porque a Task 4 precisa adicionar um 4º item ("Playground") nessa nav, e adicionar em 4 lugares manualmente seria repetir o mesmo erro. Nenhuma mudança visual: o HTML/classes renderizados são idênticos aos de hoje.

- [ ] **Step 1: Escrever o teste que falha**

Criar `apps/web/__tests__/AdminNav.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AdminNav } from "@/components/AdminNav";

describe("AdminNav", () => {
  it("renderiza o item ativo como texto (não link) e os demais como links", () => {
    render(<AdminNav active="dashboard" />);

    expect(screen.getByText("Dashboard").closest("a")).toBeNull();
    expect(screen.getByText("Tenants").closest("a")).toHaveAttribute("href", "/admin/tenants");
    expect(screen.getByText("Playground").closest("a")).toHaveAttribute(
      "href",
      "/admin/playground",
    );
  });

  it("marca playground como ativo quando active='playground'", () => {
    render(<AdminNav active="playground" />);

    expect(screen.getByText("Playground").closest("a")).toBeNull();
    expect(screen.getByText("Dashboard").closest("a")).toHaveAttribute("href", "/admin");
  });

  it("renderiza o botão Sair", () => {
    render(<AdminNav active="tenants" />);

    expect(screen.getByRole("button", { name: "Sair" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/web && npx --yes pnpm@9 test -- AdminNav`
Expected: FAIL — `@/components/AdminNav` não existe.

- [ ] **Step 3: Criar `AdminNav`**

Criar `apps/web/src/components/AdminNav.tsx`:

```tsx
import Link from "next/link";

import { adminLogout } from "@/app/admin/actions";

type AdminNavItem = "dashboard" | "tenants" | "playground";

const ITEMS: { key: AdminNavItem; href: string; label: string }[] = [
  { key: "dashboard", href: "/admin", label: "Dashboard" },
  { key: "tenants", href: "/admin/tenants", label: "Tenants" },
  { key: "playground", href: "/admin/playground", label: "Playground" },
];

export function AdminNav({ active }: { active: AdminNavItem }) {
  return (
    <nav className="flex w-14 shrink-0 flex-col items-center justify-between bg-ink py-5">
      <div className="flex flex-col items-center gap-6">
        <span className="font-display text-2xl font-semibold text-ground" aria-label="Admin">
          A.
        </span>
        {ITEMS.map((item) =>
          item.key === active ? (
            <span
              key={item.key}
              className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground [writing-mode:vertical-rl]"
            >
              {item.label}
            </span>
          ) : (
            <Link
              key={item.key}
              href={item.href}
              className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
            >
              {item.label}
            </Link>
          ),
        )}
      </div>
      <form action={adminLogout}>
        <button
          type="submit"
          className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
        >
          Sair
        </button>
      </form>
    </nav>
  );
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/web && npx --yes pnpm@9 test -- AdminNav`
Expected: PASS (3/3).

- [ ] **Step 5: Usar `AdminNav` nas 3 páginas existentes**

Substituir `apps/web/src/app/admin/page.tsx` por:

```tsx
import { AdminDashboardPanel } from "@/components/AdminDashboardPanel";
import { AdminNav } from "@/components/AdminNav";

export default function AdminDashboardPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <AdminNav active="dashboard" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <AdminDashboardPanel />
      </main>
    </div>
  );
}
```

Substituir `apps/web/src/app/admin/tenants/page.tsx` por:

```tsx
import { AdminNav } from "@/components/AdminNav";
import { AdminTenantsList } from "@/components/AdminTenantsList";

export default function AdminTenantsPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <AdminNav active="tenants" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <AdminTenantsList />
      </main>
    </div>
  );
}
```

Substituir `apps/web/src/app/admin/tenants/[id]/page.tsx` por:

```tsx
import { AdminNav } from "@/components/AdminNav";
import { AdminTenantDetail } from "@/components/AdminTenantDetail";

export default async function AdminTenantDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <div className="flex h-screen overflow-hidden">
      <AdminNav active="tenants" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <AdminTenantDetail tenantId={id} />
      </main>
    </div>
  );
}
```

- [ ] **Step 6: Rodar toda a suíte, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde — as páginas ainda renderizam (nenhum teste existente cobre o HTML da nav diretamente, só os componentes de conteúdo, então nada deveria quebrar); o build lista `/admin`, `/admin/tenants`, `/admin/tenants/[id]` normalmente.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/components/AdminNav.tsx apps/web/src/app/admin/page.tsx apps/web/src/app/admin/tenants/page.tsx "apps/web/src/app/admin/tenants/[id]/page.tsx" apps/web/__tests__/AdminNav.test.tsx
git commit -m "refactor(web): extrair AdminNav compartilhado entre as páginas do admin"
```

---

### Task 4: `web` — página e painel do playground

**Files:**
- Create: `apps/web/src/components/AdminPlaygroundPanel.tsx`
- Create: `apps/web/src/app/admin/playground/page.tsx`
- Test: `apps/web/__tests__/AdminPlaygroundPanel.test.tsx`

**Interfaces:**
- Consumes: `adminBackendFetch` (`@/lib/admin-client-api`, já existente); `AdminNav` (Task 3); `GET platform-admin/tenants` (já existente, devolve `{id, name, ...}[]`); `POST platform-admin/playground/messages` e `DELETE platform-admin/playground/conversations/{tenant_id}/{session_id}` (Task 2).

- [ ] **Step 1: Escrever o teste que falha**

Criar `apps/web/__tests__/AdminPlaygroundPanel.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminPlaygroundPanel } from "@/components/AdminPlaygroundPanel";
import { adminBackendFetch } from "@/lib/admin-client-api";

vi.mock("@/lib/admin-client-api", () => ({
  adminBackendFetch: vi.fn(),
}));

const mockedFetch = adminBackendFetch as ReturnType<typeof vi.fn>;

const TENANTS = [
  { id: "t1", name: "Escritório A", status: "active", credit_balance: 100, created_at: "2026-07-01T00:00:00Z", whatsapp_connected: false },
  { id: "t2", name: "Escritório B", status: "active", credit_balance: 50, created_at: "2026-07-01T00:00:00Z", whatsapp_connected: true },
];

beforeEach(() => {
  mockedFetch.mockReset();
});

function mockTenantsThenMessage(messageResponse: unknown) {
  mockedFetch.mockImplementation(async (path: string) => {
    if (path === "platform-admin/tenants") {
      return { ok: true, json: async () => TENANTS };
    }
    return { ok: true, json: async () => messageResponse };
  });
}

describe("AdminPlaygroundPanel", () => {
  it("carrega os tenants e permite escolher um", async () => {
    mockTenantsThenMessage({ responses: [], tokens_used: null, current_agent: null, grouped: false });

    render(<AdminPlaygroundPanel />);

    await waitFor(() => expect(screen.getByText("Escritório A")).toBeInTheDocument());
    expect(screen.getByText("Escritório B")).toBeInTheDocument();
  });

  it("envia mensagem e renderiza a resposta com a tag do agente", async () => {
    mockTenantsThenMessage({
      responses: ["Olá! Sou a secretária, como posso ajudar?"],
      tokens_used: 150,
      current_agent: "agente_condominial",
      grouped: false,
    });

    render(<AdminPlaygroundPanel />);
    await waitFor(() => expect(screen.getByText("Escritório A")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Digite uma mensagem..."), {
      target: { value: "tenho uma dúvida sobre condomínio" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));

    await waitFor(() =>
      expect(screen.getByText("Olá! Sou a secretária, como posso ajudar?")).toBeInTheDocument(),
    );
    expect(screen.getByText("tenho uma dúvida sobre condomínio")).toBeInTheDocument();
    expect(screen.getByText("Condominial")).toBeInTheDocument();
  });

  it("mostra aviso quando a mensagem é agrupada pelo debounce", async () => {
    mockTenantsThenMessage({ responses: [], tokens_used: null, current_agent: null, grouped: true });

    render(<AdminPlaygroundPanel />);
    await waitFor(() => expect(screen.getByText("Escritório A")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Digite uma mensagem..."), {
      target: { value: "oi" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));

    await waitFor(() =>
      expect(screen.getByText(/agrupada à execução em andamento/i)).toBeInTheDocument(),
    );
  });

  it("mostra erro inline quando o agente falha, sem apagar o histórico", async () => {
    mockedFetch.mockImplementation(async (path: string) => {
      if (path === "platform-admin/tenants") {
        return { ok: true, json: async () => TENANTS };
      }
      return { ok: false, status: 502 };
    });

    render(<AdminPlaygroundPanel />);
    await waitFor(() => expect(screen.getByText("Escritório A")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Digite uma mensagem..."), {
      target: { value: "oi" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));

    await waitFor(() =>
      expect(screen.getByText(/não foi possível falar com o agente/i)).toBeInTheDocument(),
    );
    expect(screen.getByText("oi")).toBeInTheDocument();
  });

  it("Nova conversa limpa o histórico e a tag volta pra Secretária", async () => {
    mockTenantsThenMessage({
      responses: ["oi!"],
      tokens_used: 10,
      current_agent: "agente_contratos",
      grouped: false,
    });

    render(<AdminPlaygroundPanel />);
    await waitFor(() => expect(screen.getByText("Escritório A")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Digite uma mensagem..."), {
      target: { value: "oi" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));
    await waitFor(() => expect(screen.getByText("Contratos")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Nova conversa" }));

    expect(screen.queryByText("oi!")).not.toBeInTheDocument();
    expect(screen.getByText("Secretária")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/web && npx --yes pnpm@9 test -- AdminPlaygroundPanel`
Expected: FAIL — `@/components/AdminPlaygroundPanel` não existe.

- [ ] **Step 3: Criar `AdminPlaygroundPanel`**

Criar `apps/web/src/components/AdminPlaygroundPanel.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";

import { adminBackendFetch } from "@/lib/admin-client-api";

type Tenant = { id: string; name: string };

type ChatMessage = {
  id: string;
  role: "dev" | "agent" | "system";
  content: string;
  tokensUsed?: number | null;
};

type PlaygroundResponse = {
  responses: string[];
  tokens_used: number | null;
  current_agent: string | null;
  grouped: boolean;
};

const AGENT_LABELS: Record<string, string> = {
  agente_secretaria: "Secretária",
  agente_condominial: "Condominial",
  agente_contratos: "Contratos",
  agente_direito_consumidor: "Direito do Consumidor",
};

function agentLabel(currentAgent: string | null): string {
  if (!currentAgent) return "Secretária";
  return AGENT_LABELS[currentAgent] ?? currentAgent;
}

function newSessionId(): string {
  return crypto.randomUUID();
}

export function AdminPlaygroundPanel() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState<string>("");
  const [sessionId, setSessionId] = useState<string>(() => newSessionId());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [currentAgent, setCurrentAgent] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const previousSession = useRef<{ tenantId: string; sessionId: string } | null>(null);

  useEffect(() => {
    async function loadTenants() {
      const response = await adminBackendFetch("platform-admin/tenants");
      if (response.ok) {
        const data = (await response.json()) as Tenant[];
        setTenants(data);
        if (data.length > 0) {
          setTenantId((current) => current || data[0].id);
        }
      }
    }
    void loadTenants();
  }, []);

  function resetConversation(nextTenantId: string) {
    if (previousSession.current) {
      void adminBackendFetch(
        `platform-admin/playground/conversations/${previousSession.current.tenantId}/${previousSession.current.sessionId}`,
        { method: "DELETE" },
      );
    }
    previousSession.current = { tenantId: nextTenantId, sessionId };
    setSessionId(newSessionId());
    setMessages([]);
    setCurrentAgent(null);
  }

  function handleTenantChange(nextTenantId: string) {
    resetConversation(tenantId);
    setTenantId(nextTenantId);
  }

  function handleNewConversation() {
    resetConversation(tenantId);
  }

  async function handleSend() {
    const message = input.trim();
    if (!message || !tenantId || sending) return;

    const devMessage: ChatMessage = { id: crypto.randomUUID(), role: "dev", content: message };
    setMessages((prev) => [...prev, devMessage]);
    setInput("");
    setSending(true);

    try {
      const response = await adminBackendFetch("platform-admin/playground/messages", {
        method: "POST",
        body: JSON.stringify({ tenant_id: tenantId, session_id: sessionId, message }),
      });

      if (!response.ok) {
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "system",
            content: "Não foi possível falar com o agente agora.",
          },
        ]);
        return;
      }

      const data = (await response.json()) as PlaygroundResponse;

      if (data.grouped) {
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "system",
            content: "Mensagem agrupada à execução em andamento — aguarde a resposta.",
          },
        ]);
        return;
      }

      setCurrentAgent(data.current_agent);
      setMessages((prev) => [
        ...prev,
        ...data.responses.map((content, index) => ({
          id: crypto.randomUUID(),
          role: "agent" as const,
          content,
          tokensUsed: index === data.responses.length - 1 ? data.tokens_used : undefined,
        })),
      ]);
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "system",
          content: "Não foi possível falar com o agente agora.",
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex h-full flex-col p-8">
      <div className="flex items-center justify-between gap-4 border-b border-line pb-4">
        <div className="flex items-center gap-3">
          <label htmlFor="tenant-select" className="text-sm text-muted">
            Tenant
          </label>
          <select
            id="tenant-select"
            value={tenantId}
            onChange={(event) => handleTenantChange(event.target.value)}
            className="rounded-sm border border-line bg-surface px-3 py-1.5 text-sm"
          >
            {tenants.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>
                {tenant.name}
              </option>
            ))}
          </select>
          <span className="rounded-full bg-accent-soft px-3 py-1 font-mono text-[10px] uppercase tracking-[0.15em] text-accent">
            {agentLabel(currentAgent)}
          </span>
        </div>
        <button
          type="button"
          onClick={handleNewConversation}
          className="rounded-sm border border-line px-3 py-1.5 text-sm text-ink hover:bg-surface"
        >
          Nova conversa
        </button>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto py-4">
        {messages.map((message) => (
          <div
            key={message.id}
            className={
              message.role === "dev"
                ? "ml-auto max-w-md rounded-sm bg-accent px-4 py-2 text-sm text-surface"
                : message.role === "system"
                  ? "mx-auto max-w-md rounded-sm border border-line px-4 py-2 text-center text-sm text-muted"
                  : "max-w-md rounded-sm border border-line bg-surface px-4 py-2 text-sm text-ink"
            }
          >
            {message.content}
            {typeof message.tokensUsed === "number" && (
              <span className="ml-2 font-mono text-[10px] text-muted">
                {message.tokensUsed} tokens
              </span>
            )}
          </div>
        ))}
        {sending && <p className="text-sm text-muted">agente digitando...</p>}
      </div>

      <div className="flex gap-2 border-t border-line pt-4">
        <input
          type="text"
          value={input}
          placeholder="Digite uma mensagem..."
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void handleSend();
          }}
          className="flex-1 rounded-sm border border-line bg-surface px-3 py-2 text-sm"
        />
        <button
          type="button"
          onClick={() => void handleSend()}
          disabled={sending || !input.trim()}
          className="rounded-sm bg-accent px-4 py-2 text-sm font-medium text-surface disabled:opacity-60"
        >
          Enviar
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/web && npx --yes pnpm@9 test -- AdminPlaygroundPanel`
Expected: PASS (5/5).

- [ ] **Step 5: Página**

Criar `apps/web/src/app/admin/playground/page.tsx`:

```tsx
import { AdminNav } from "@/components/AdminNav";
import { AdminPlaygroundPanel } from "@/components/AdminPlaygroundPanel";

export default function AdminPlaygroundPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <AdminNav active="playground" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <AdminPlaygroundPanel />
      </main>
    </div>
  );
}
```

- [ ] **Step 6: Rodar toda a suíte, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde; build lista `/admin/playground`.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/components/AdminPlaygroundPanel.tsx apps/web/src/app/admin/playground/page.tsx apps/web/__tests__/AdminPlaygroundPanel.test.tsx
git commit -m "feat(web): playground de agentes no painel de administração"
```

---

### Task 5: Atualizar `CLAUDE.md` e verificação local

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: tudo das tasks anteriores.

- [ ] **Step 1: Atualizar o CLAUDE.md**

- Seção "Painel de Administração da Plataforma": adicionar um parágrafo sobre o playground — chat de teste em `/admin/playground`, chama o `agents` com `send_to_whatsapp=false`, efêmero (sem persistência no `api`, sem débito de créditos), mostra a tag do agente ativo (`current_agent`). Rota nova: `POST /api/v1/platform-admin/playground/messages`, `DELETE /api/v1/platform-admin/playground/conversations/{tenant_id}/{session_id}`.
- Seção "Agents Service": no resumo de `POST /messages`, mencionar o novo campo `send_to_whatsapp` (default `true`) e que a resposta agora inclui `current_agent`.
- Seção "Estado atual do repositório": `agents` ganhou o flag `send_to_whatsapp`; `api`/`web` ganharam o playground.

- [ ] **Step 2: Build e verificação local**

```bash
docker compose up -d --build agents api web
```

1. `curl -s http://localhost:8001/agents` — confirma o `agents` de pé (rota já existente).
2. Login de platform_admin (mesmo de sempre) e `POST /api/v1/platform-admin/playground/messages` com um `tenant_id` real do seed, `session_id` qualquer e uma mensagem — confirmar `200` com `responses`/`current_agent` no corpo (`current_agent` deve vir `"agente_secretaria"` ou `null` na primeira mensagem, dependendo do prompt).
3. Repetir a chamada com o mesmo `tenant_id`/`session_id` fazendo uma pergunta que dispare transferência pra um especialista (ex: pergunta sobre condomínio) — confirmar que `current_agent` muda pra `"agente_condominial"` na resposta seguinte.
4. `curl -s http://localhost:8000/api/v1/platform-admin/playground/messages` com `tenant_id` inexistente → `404`.
5. `DELETE /api/v1/platform-admin/playground/conversations/{tenant_id}/{session_id}` → `204`.
6. Acessar `http://localhost:3001/admin/playground` logado — escolher um tenant, enviar mensagem, confirmar que a resposta aparece e a tag do agente atualiza; clicar "Nova conversa" e confirmar que o chat limpa.
7. Confirmar (via `psql`/log) que **nenhuma** linha nova apareceu em `conversations`/`messages`/`credit_transactions` depois dos testes acima.

Expected: todos os passos funcionam; o passo 7 (nada persistido) é o mais importante de confirmar, já que é a garantia central do design "efêmero".

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: playground de agentes documentado no CLAUDE.md"
```
