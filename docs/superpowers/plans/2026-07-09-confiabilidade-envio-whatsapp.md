# Confiabilidade de Envio no WhatsApp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir a falha silenciosa de entrega de mensagens do WhatsApp (canal automatizado do `agents`), com retry curto pra falha transitória, rate limiting defensivo por número, sinalização visível de falha ("Não entregue" no painel) e handoff automático pra humano quando o `worker` esgota as tentativas de falar com o `agents`.

**Architecture:** `apps/agents/clients/whatsapp.py` ganha retry e rate limiting no ponto único de chamada à Graph API (`_safe_request`). A rota `POST /messages` do `agents` para de descartar o resultado do envio e devolve `delivery_failures` (índices das mensagens não entregues). `apps/worker` grava esse resultado em `messages.delivery_status` (coluna nova, migration em `apps/api`) e, separadamente, ganha tratamento de última tentativa (mesmo padrão já usado na ingestão de KB) ao falar com o `agents`. `apps/web` mostra um badge "Não entregue" na mensagem afetada.

**Tech Stack:** FastAPI + httpx + redis.asyncio (agents), FastAPI + SQLAlchemy async (api), Arq + SQLAlchemy Core (worker), Next.js 15 + React (web).

## Global Constraints

- Falha de entrega nunca é reportada como sucesso; `tokens_used`/créditos não mudam quando a entrega falha (o custo do LLM já ocorreu).
- Retry só para erro transitório: `httpx.TimeoutException`, `httpx.ConnectError`, ou resposta HTTP 5xx. Erro 4xx falha imediatamente, sem retry.
- Até 3 tentativas totais, com backoff fixo de `0.5s` antes da 2ª tentativa e `1s` antes da 3ª.
- Rate limiting só no canal automatizado (`agents`) — não no takeover humano (`apps/api`).
- Token bucket via Redis (`INCR`/`EXPIRE 1s`), chave `whatsapp:ratelimit:{phone_number_id}`, limite configurável via `WHATSAPP_RATE_LIMIT_PER_SECOND` (default `10`), teto de espera de `5s` — se não liberar, tratado como falha transitória (entra no mesmo retry).
- Sem infraestrutura nova (sem dead-letter table dedicada, sem fila de replay, sem tela de admin nova).
- `messages.delivery_status` (`String`, nullable, `sent`|`failed`) — só significativo pra `sender_type` `agent`/`human`; mensagens de contato e mensagens antigas ficam `NULL`.
- Handoff automático pra `human` só dispara quando o `worker` esgota as tentativas de **chamar** o `agents` (falha de rede/timeout/5xx do próprio `agents`) — nunca por falha de **entrega** ao WhatsApp (essa é tratada via `delivery_status`, sem mudar o estado da conversa).
- Mensagens/comentários em pt-BR com acentuação correta.
- Comandos: `apps/agents` → `cd apps/agents && uv run pytest tests/unit -q` (lint via `uvx ruff check .` nos arquivos tocados — `ruff` não é dependência declarada nesse serviço, gap pré-existente, não desta feature). `apps/api` → `uv run pytest tests/unit`, `uv run ruff check .`, `uv run ruff format --check .`. `apps/worker` → mesmos comandos do `api`, dentro de `apps/worker`. `apps/web` → `npx --yes pnpm@9 test`, `npx --yes pnpm@9 lint`, `npx --yes pnpm@9 build`.

---

### Task 1: `agents` — retry transitório no envio à Graph API

**Files:**
- Modify: `apps/agents/clients/whatsapp.py`
- Test: `apps/agents/tests/unit/test_whatsapp_client.py` (create)

**Interfaces:**
- Consumes: nada de outras tasks.
- Produces: `WhatsAppClient._safe_request` (e por extensão `send_text_message`/`send_document_message`) agora retenta até 3 vezes pra falha transitória, sem retry pra 4xx. Retorno inalterado: `{"success": bool, "data": ..., "error": ...}`. A Task 2 vai modificar `_safe_request` de novo pra inserir a checagem de rate limit no topo de cada iteração do loop `for attempt in range(1, _MAX_ATTEMPTS + 1):` — os nomes `attempt`, `_MAX_ATTEMPTS` e `_RETRY_BACKOFF_SECONDS` (lista indexada por `attempt - 1`) são o contrato que a Task 2 depende.

- [ ] **Step 1: Escrever os testes que falham**

Criar `apps/agents/tests/unit/test_whatsapp_client.py`:

```python
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from clients.whatsapp import WhatsAppClient


@pytest.fixture
def client():
    return WhatsAppClient("111222333", "token-do-tenant")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Backoff real deixaria os testes lentos — tempo não é o que testamos aqui."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


class TestSendTextMessageRetry:
    async def test_sucesso_na_primeira_tentativa_nao_faz_retry(self, client, monkeypatch) -> None:
        response = httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is True
        assert request_mock.await_count == 1

    async def test_erro_4xx_nao_faz_retry(self, client, monkeypatch) -> None:
        response = httpx.Response(401, text='{"error":"Invalid OAuth access token"}')
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is False
        assert request_mock.await_count == 1

    async def test_erro_5xx_faz_retry_e_se_recupera_na_segunda_tentativa(
        self, client, monkeypatch
    ) -> None:
        error_response = httpx.Response(503, text="service unavailable")
        ok_response = httpx.Response(200, json={"messages": [{"id": "wamid.2"}]})
        request_mock = AsyncMock(side_effect=[error_response, ok_response])
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is True
        assert request_mock.await_count == 2

    async def test_erro_5xx_esgota_as_tres_tentativas(self, client, monkeypatch) -> None:
        error_response = httpx.Response(500, text="internal error")
        request_mock = AsyncMock(return_value=error_response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is False
        assert request_mock.await_count == 3

    async def test_timeout_faz_retry_e_se_recupera_na_terceira_tentativa(
        self, client, monkeypatch
    ) -> None:
        ok_response = httpx.Response(200, json={"messages": [{"id": "wamid.3"}]})
        request_mock = AsyncMock(
            side_effect=[
                httpx.TimeoutException("timeout"),
                httpx.TimeoutException("timeout"),
                ok_response,
            ]
        )
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is True
        assert request_mock.await_count == 3

    async def test_erro_de_conexao_esgota_as_tentativas(self, client, monkeypatch) -> None:
        request_mock = AsyncMock(side_effect=httpx.ConnectError("conexão recusada"))
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is False
        assert request_mock.await_count == 3
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/agents && UV_PROJECT_ENVIRONMENT=/tmp/agents-venv uv sync --quiet && UV_PROJECT_ENVIRONMENT=/tmp/agents-venv uv run pytest tests/unit/test_whatsapp_client.py -v`

(O `.venv` do projeto pode estar com dono `root` no sandbox local — nesse caso, use um venv temporário via `UV_PROJECT_ENVIRONMENT` apontando pra fora do repo, como acima. Se `uv run` direto funcionar sem erro de permissão, pode omitir essa variável.)

Expected: os 6 testes FALHAM (nenhum ainda existe retry — todos esperam `await_count` maior que 1 onde o código atual sempre faz 1 tentativa só).

- [ ] **Step 3: Implementar o retry**

Substituir o conteúdo de `apps/agents/clients/whatsapp.py` por:

```python
import asyncio
import httpx
from dotenv import load_dotenv
import os
import time
from loguru import logger

load_dotenv()

GRAPH_API_BASE_URL = os.getenv("GRAPH_API_BASE_URL", "https://graph.facebook.com")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v23.0")

# Retry curto só para falha transitória (timeout/conexão/5xx) — 4xx nunca é
# retried (não é transitório, retry só desperdiça tempo).
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = [0.5, 1]


class WhatsAppClient:
    """Cliente da WhatsApp Cloud API (Graph API da Meta).

    As credenciais (phone_number_id + access_token) são por tenant e chegam
    em cada request — este serviço não armazena nem resolve credenciais.
    """

    def __init__(self, phone_number_id: str, access_token: str):
        self._phone_number_id = phone_number_id
        self._access_token = access_token
        self._base_url = f"{GRAPH_API_BASE_URL}/{GRAPH_API_VERSION}"
        self._client: httpx.AsyncClient | None = None
        logger.info(
            "WhatsAppClient inicializado | phone_number_id={}", phone_number_id
        )

    # ---------- SESSION LIFECYCLE ----------
    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=15)
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15)
        return self._client

    # ---------- CORE SAFE REQUEST ----------
    async def _safe_request(self, method: str, url: str, **kwargs):
        client = self._get_client()
        last_error: dict = {"success": False, "data": None, "error": "Erro desconhecido"}

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            started_at = time.perf_counter()
            try:
                logger.info(
                    "Executando requisição à Graph API | method={} url={} tentativa={}",
                    method, url, attempt,
                )
                response = await client.request(method, url, **kwargs)

                if response.is_error:
                    logger.warning(
                        "Resposta HTTP não OK | method={} url={} status={} body={}",
                        method, url, response.status_code, response.text,
                    )
                    last_error = {
                        "success": False,
                        "data": None,
                        "error": f"HTTP {response.status_code}: {response.text}",
                    }
                    if response.status_code < 500:
                        # 4xx não é transitório — falha imediata, sem retry.
                        return last_error
                    if attempt < _MAX_ATTEMPTS:
                        await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                        continue
                    return last_error

                try:
                    data = response.json()
                except Exception:
                    data = response.text

                elapsed = round(time.perf_counter() - started_at, 3)
                logger.info(
                    "Requisição concluída | method={} url={} status={} elapsed={}s",
                    method, url, response.status_code, elapsed,
                )
                return {"success": True, "data": data, "error": None}

            except httpx.TimeoutException:
                logger.error(
                    "Timeout ao acessar Graph API | method={} url={} tentativa={}",
                    method, url, attempt,
                )
                last_error = {
                    "success": False,
                    "data": None,
                    "error": "Timeout ao acessar Graph API",
                }
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                return last_error

            except httpx.ConnectError as e:
                logger.error(
                    "Erro de conexão com Graph API | method={} url={} error={} tentativa={}",
                    method, url, e, attempt,
                )
                last_error = {"success": False, "data": None, "error": f"Erro de conexão: {e}"}
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                return last_error

            except httpx.RequestError as e:
                logger.error(
                    "Erro de requisição à Graph API | method={} url={} error={}", method, url, e
                )
                return {"success": False, "data": None, "error": f"Erro de requisição: {e}"}

            except Exception as e:
                logger.exception(
                    "Erro inesperado ao acessar Graph API | method={} url={}", method, url
                )
                return {"success": False, "data": None, "error": f"Erro inesperado: {e}"}

        return last_error

    # ---------- HEADERS ----------
    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._access_token}",
        }

    # ---------- MESSAGES ----------
    async def send_text_message(self, to: str, text: str):
        url = f"{self._base_url}/{self._phone_number_id}/messages"
        logger.info("Enviando mensagem de texto | to={}", to)
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }
        return await self._safe_request("POST", url, headers=self._headers(), json=payload)

    async def send_document_message(
        self, to: str, link: str, filename: str | None = None, caption: str | None = None
    ):
        url = f"{self._base_url}/{self._phone_number_id}/messages"
        logger.info("Enviando documento | to={} link={}", to, link)
        document: dict = {"link": link}
        if filename:
            document["filename"] = filename
        if caption:
            document["caption"] = caption
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "document",
            "document": document,
        }
        return await self._safe_request("POST", url, headers=self._headers(), json=payload)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/agents && UV_PROJECT_ENVIRONMENT=/tmp/agents-venv uv run pytest tests/unit/test_whatsapp_client.py -v`
Expected: PASS (6/6).

- [ ] **Step 5: Rodar a suíte completa e lint dos arquivos tocados**

Run: `cd apps/agents && UV_PROJECT_ENVIRONMENT=/tmp/agents-venv uv run pytest tests/unit -q`
Expected: todos PASS (nenhum teste existente de `test_routes.py` quebra — o mock de `WhatsAppClient` nesses testes substitui a classe inteira, não chama `_safe_request` de verdade).

Run: `cd apps/agents && uvx ruff check clients/whatsapp.py tests/unit/test_whatsapp_client.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add apps/agents/clients/whatsapp.py apps/agents/tests/unit/test_whatsapp_client.py
git commit -m "feat(agents): retry curto para falha transitória no envio via Graph API"
```

---

### Task 2: `agents` — rate limiting por número via Redis

**Files:**
- Create: `apps/agents/clients/ratelimit.py`
- Modify: `apps/agents/clients/whatsapp.py`
- Test: `apps/agents/tests/unit/test_ratelimit.py` (create)
- Test: `apps/agents/tests/unit/test_whatsapp_client.py` (modify)
- Modify: `.env.example`

**Interfaces:**
- Consumes: o loop `for attempt in range(1, _MAX_ATTEMPTS + 1):` e a lista `_RETRY_BACKOFF_SECONDS` de `apps/agents/clients/whatsapp.py` (Task 1).
- Produces: `acquire_rate_limit_slot(phone_number_id: str) -> bool` em `clients/ratelimit.py` — `True` se conseguiu uma vaga no bucket do segundo atual, `False` se esgotou o teto de espera de 5s.

- [ ] **Step 1: Escrever os testes do rate limiter que falham**

Criar `apps/agents/tests/unit/test_ratelimit.py`:

```python
import asyncio
from unittest.mock import AsyncMock

import pytest

import clients.ratelimit as ratelimit_module
from clients.ratelimit import acquire_rate_limit_slot


class FakeRedis:
    def __init__(self):
        self.store: dict[str, int] = {}

    async def get(self, key):
        return self.store.get(key)

    async def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key, seconds):
        pass

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(ratelimit_module.aioredis, "Redis", lambda **kwargs: fake)
    return fake


class TestAcquireRateLimitSlot:
    async def test_libera_imediatamente_abaixo_do_limite(self, fake_redis, monkeypatch) -> None:
        monkeypatch.setattr(ratelimit_module, "WHATSAPP_RATE_LIMIT_PER_SECOND", 5)

        acquired = await acquire_rate_limit_slot("111222333")

        assert acquired is True
        assert fake_redis.store["whatsapp:ratelimit:111222333"] == 1

    async def test_nega_apos_esgotar_o_limite_do_segundo(self, fake_redis, monkeypatch) -> None:
        monkeypatch.setattr(ratelimit_module, "WHATSAPP_RATE_LIMIT_PER_SECOND", 1)
        fake_redis.store["whatsapp:ratelimit:111222333"] = 1

        acquired = await acquire_rate_limit_slot("111222333")

        assert acquired is False

    async def test_numeros_diferentes_tem_buckets_independentes(
        self, fake_redis, monkeypatch
    ) -> None:
        monkeypatch.setattr(ratelimit_module, "WHATSAPP_RATE_LIMIT_PER_SECOND", 1)
        fake_redis.store["whatsapp:ratelimit:AAA"] = 1

        acquired = await acquire_rate_limit_slot("BBB")

        assert acquired is True
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/agents && UV_PROJECT_ENVIRONMENT=/tmp/agents-venv uv run pytest tests/unit/test_ratelimit.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'clients.ratelimit'`.

- [ ] **Step 3: Implementar o rate limiter**

Criar `apps/agents/clients/ratelimit.py`:

```python
"""Rate limiter simples (contador por segundo) para envio via Graph API.

Protege contra rajadas que ultrapassem o limite de mensagens/segundo por
número — mesma infraestrutura Redis já usada no debounce
(services/concat_messages.py), sem depender de biblioteca externa.
"""

import asyncio
import os

import redis.asyncio as aioredis
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = os.getenv("REDIS_PORT")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
WHATSAPP_RATE_LIMIT_PER_SECOND = int(os.getenv("WHATSAPP_RATE_LIMIT_PER_SECOND", "10"))

_MAX_WAIT_SECONDS = 5
_POLL_INTERVAL_SECONDS = 0.1


async def acquire_rate_limit_slot(phone_number_id: str) -> bool:
    """Consome 1 unidade do bucket do segundo atual para este número.

    Espera até _MAX_WAIT_SECONDS por uma vaga; devolve True se conseguiu,
    False se o teto de espera foi atingido (o chamador trata como falha
    transitória e decide se tenta de novo).
    """
    r = aioredis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, decode_responses=True
    )
    key = f"whatsapp:ratelimit:{phone_number_id}"
    waited = 0.0
    try:
        while True:
            current = await r.get(key)
            current_count = int(current) if current else 0
            if current_count < WHATSAPP_RATE_LIMIT_PER_SECOND:
                count = await r.incr(key)
                if count == 1:
                    await r.expire(key, 1)
                return True
            if waited >= _MAX_WAIT_SECONDS:
                logger.warning(
                    "Rate limit não liberado a tempo | phone_number_id={} limite={}",
                    phone_number_id, WHATSAPP_RATE_LIMIT_PER_SECOND,
                )
                return False
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            waited += _POLL_INTERVAL_SECONDS
    finally:
        await r.aclose()
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/agents && UV_PROJECT_ENVIRONMENT=/tmp/agents-venv uv run pytest tests/unit/test_ratelimit.py -v`
Expected: PASS (3/3).

- [ ] **Step 5: Escrever os testes de integração com `_safe_request` que falham**

Adicionar ao final de `apps/agents/tests/unit/test_whatsapp_client.py`:

```python

class TestSendTextMessageRateLimit:
    async def test_rate_limit_negado_uma_vez_ainda_tenta_de_novo(self, client, monkeypatch) -> None:
        import clients.whatsapp as whatsapp_module

        ok_response = httpx.Response(200, json={"messages": [{"id": "wamid.4"}]})
        request_mock = AsyncMock(return_value=ok_response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)
        acquire_mock = AsyncMock(side_effect=[False, True])
        monkeypatch.setattr(whatsapp_module, "acquire_rate_limit_slot", acquire_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is True
        assert acquire_mock.await_count == 2
        assert request_mock.await_count == 1

    async def test_rate_limit_negado_em_todas_as_tentativas_falha(self, client, monkeypatch) -> None:
        import clients.whatsapp as whatsapp_module

        acquire_mock = AsyncMock(return_value=False)
        monkeypatch.setattr(whatsapp_module, "acquire_rate_limit_slot", acquire_mock)
        request_mock = AsyncMock()
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is False
        assert acquire_mock.await_count == 3
        request_mock.assert_not_awaited()
```

- [ ] **Step 6: Rodar e ver falhar**

Run: `cd apps/agents && UV_PROJECT_ENVIRONMENT=/tmp/agents-venv uv run pytest tests/unit/test_whatsapp_client.py -v -k RateLimit`
Expected: FAIL — `AttributeError: module 'clients.whatsapp' has no attribute 'acquire_rate_limit_slot'` (ainda não importado em `whatsapp.py`).

- [ ] **Step 7: Integrar o rate limiter em `_safe_request`**

Em `apps/agents/clients/whatsapp.py`, adicionar o import (após a linha `from loguru import logger`):

```python
from clients.ratelimit import acquire_rate_limit_slot
```

Trocar o início do loop dentro de `_safe_request` — de:

```python
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            started_at = time.perf_counter()
            try:
```

para:

```python
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            acquired = await acquire_rate_limit_slot(self._phone_number_id)
            if not acquired:
                last_error = {
                    "success": False,
                    "data": None,
                    "error": "Rate limit excedido — sem vaga liberada a tempo",
                }
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                return last_error

            started_at = time.perf_counter()
            try:
```

- [ ] **Step 8: Rodar e ver passar**

Run: `cd apps/agents && UV_PROJECT_ENVIRONMENT=/tmp/agents-venv uv run pytest tests/unit/test_whatsapp_client.py -v`
Expected: PASS em todos (8/8: os 6 da Task 1 + os 2 novos).

- [ ] **Step 9: Adicionar a env ao `.env.example`**

Em `.env.example`, na seção `# Agents service`, adicionar após a linha `AGENTS_API_KEY=`:

```dotenv
# Rate limit defensivo de envio no WhatsApp (mensagens/segundo por número) —
# valor bem abaixo do limite real da Cloud API, só proteção contra rajada.
WHATSAPP_RATE_LIMIT_PER_SECOND=10
```

- [ ] **Step 10: Rodar a suíte completa e lint**

Run: `cd apps/agents && UV_PROJECT_ENVIRONMENT=/tmp/agents-venv uv run pytest tests/unit -q`
Expected: todos PASS.

Run: `cd apps/agents && uvx ruff check clients/ratelimit.py clients/whatsapp.py tests/unit/test_ratelimit.py tests/unit/test_whatsapp_client.py`
Expected: `All checks passed!`

- [ ] **Step 11: Commit**

```bash
git add apps/agents/clients/ratelimit.py apps/agents/clients/whatsapp.py apps/agents/tests/unit/test_ratelimit.py apps/agents/tests/unit/test_whatsapp_client.py .env.example
git commit -m "feat(agents): rate limiting por número via Redis antes de cada envio"
```

---

### Task 3: `agents` — rota `/messages` propaga falhas de entrega

**Files:**
- Modify: `apps/agents/api/routes.py`
- Modify: `apps/agents/tests/unit/test_routes.py`

**Interfaces:**
- Consumes: `send_text_message` retornando `{"success": bool, ...}` (Task 1/2, já existente).
- Produces: `POST /messages` devolve um campo novo aditivo `delivery_failures: list[int]` (índices 0-based, relativos a `responses`) — usado pelo `worker` na Task 4.

- [ ] **Step 1: Atualizar os testes existentes que vão quebrar**

Em `apps/agents/tests/unit/test_routes.py`, a função `test_fluxo_feliz_envia_respostas_e_retorna_lista` tem:

```python
    assert response.json() == {
        "responses": ["resposta 1", "resposta 2"],
        "tokens_used": 1234,
        "current_agent": "agente_secretaria",
    }
```

Trocar para:

```python
    assert response.json() == {
        "responses": ["resposta 1", "resposta 2"],
        "tokens_used": 1234,
        "current_agent": "agente_secretaria",
        "delivery_failures": [],
    }
```

A função `test_send_to_whatsapp_false_nao_envia_mas_retorna_respostas` tem:

```python
    assert response.json() == {
        "responses": ["resposta 1", "resposta 2"],
        "tokens_used": 1234,
        "current_agent": "agente_condominial",
    }
```

Trocar para:

```python
    assert response.json() == {
        "responses": ["resposta 1", "resposta 2"],
        "tokens_used": 1234,
        "current_agent": "agente_condominial",
        "delivery_failures": [],
    }
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/agents && UV_PROJECT_ENVIRONMENT=/tmp/agents-venv uv run pytest tests/unit/test_routes.py -v`
Expected: FAIL nos 2 testes atualizados (a rota ainda não devolve `delivery_failures`).

- [ ] **Step 3: Escrever o teste novo que falha**

Adicionar ao final de `apps/agents/tests/unit/test_routes.py`:

```python

def test_falha_parcial_de_entrega_aparece_em_delivery_failures(client, monkeypatch) -> None:
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(
        return_value=(["resposta 1", "resposta 2"], 1234, "agente_secretaria")
    )
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    wa_cls, wa_instance = _mock_whatsapp_client(monkeypatch)
    wa_instance.send_text_message.side_effect = [
        {"success": True},
        {"success": False, "error": "HTTP 401: token inválido"},
    ]

    response = client.post("/messages", json=PAYLOAD)

    assert response.status_code == 200
    assert response.json()["delivery_failures"] == [1]
```

- [ ] **Step 4: Rodar e ver falhar**

Run: `cd apps/agents && UV_PROJECT_ENVIRONMENT=/tmp/agents-venv uv run pytest tests/unit/test_routes.py -v -k delivery_failures`
Expected: FAIL — `KeyError: 'delivery_failures'` (chave ainda não existe na resposta).

- [ ] **Step 5: Implementar a propagação na rota**

Em `apps/agents/api/routes.py`, trocar o bloco (dentro de `async def receive`):

```python
        if body.send_to_whatsapp:
            logger.info(
                "Enviando {} resposta(s) via WhatsApp | thread_id={}",
                len(response),
                thread_id,
            )
            async with WhatsAppClient(
                body.phone_number_id, body.access_token
            ) as client:
                for msg in response:
                    await client.send_text_message(body.contact_phone_number, msg)
        else:
            logger.info(
                "send_to_whatsapp=False — envio pulado | thread_id={}", thread_id
            )

        # Devolve as respostas e os tokens da execução para o chamador
        # (`worker`) persistir em `messages` e debitar os créditos.
        return {
            "responses": response,
            "tokens_used": tokens_used,
            "current_agent": current_agent,
        }
```

por:

```python
        delivery_failures: list[int] = []
        if body.send_to_whatsapp:
            logger.info(
                "Enviando {} resposta(s) via WhatsApp | thread_id={}",
                len(response),
                thread_id,
            )
            async with WhatsAppClient(
                body.phone_number_id, body.access_token
            ) as client:
                for i, msg in enumerate(response):
                    result = await client.send_text_message(body.contact_phone_number, msg)
                    if not result.get("success"):
                        logger.warning(
                            "Falha ao entregar mensagem via WhatsApp | thread_id={} indice={} erro={}",
                            thread_id, i, result.get("error"),
                        )
                        delivery_failures.append(i)
        else:
            logger.info(
                "send_to_whatsapp=False — envio pulado | thread_id={}", thread_id
            )

        # Devolve as respostas, os tokens da execução e as falhas de entrega
        # para o chamador (`worker`) persistir em `messages` e debitar
        # créditos — a cobrança independe da entrega ter funcionado (o custo
        # do LLM já ocorreu).
        return {
            "responses": response,
            "tokens_used": tokens_used,
            "current_agent": current_agent,
            "delivery_failures": delivery_failures,
        }
```

- [ ] **Step 6: Rodar e ver passar**

Run: `cd apps/agents && UV_PROJECT_ENVIRONMENT=/tmp/agents-venv uv run pytest tests/unit/test_routes.py -v`
Expected: PASS em todos.

- [ ] **Step 7: Rodar a suíte completa e lint**

Run: `cd apps/agents && UV_PROJECT_ENVIRONMENT=/tmp/agents-venv uv run pytest tests/unit -q`
Expected: todos PASS.

Run: `cd apps/agents && uvx ruff check api/routes.py tests/unit/test_routes.py`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add apps/agents/api/routes.py apps/agents/tests/unit/test_routes.py
git commit -m "feat(agents): rota /messages propaga índices de falha de entrega"
```

---

### Task 4: `api`+`worker` — persistência de `delivery_status`

**Files:**
- Create: `apps/api/alembic/versions/0007_message_delivery_status.py`
- Modify: `apps/api/app/models/message.py`
- Modify: `apps/api/app/schemas/conversations.py`
- Modify: `apps/api/app/api/v1/conversations.py`
- Modify: `apps/api/tests/unit/test_conversations_routes.py`
- Modify: `apps/worker/app/tables.py`
- Modify: `apps/worker/app/clients/agents.py`
- Modify: `apps/worker/app/tasks/messages.py`
- Modify: `apps/worker/tests/unit/test_process_inbound_message.py`
- Test: `apps/worker/tests/unit/test_persist_agent_responses.py` (create)

**Interfaces:**
- Consumes: `delivery_failures: list[int]` devolvido por `POST /messages` do `agents` (Task 3).
- Produces: `messages.delivery_status` (`sent`|`failed`|`NULL`); `MessageOut.delivery_status`; `_persist_agent_responses(session, tenant_id, conversation_id, responses, tokens_used=0, credits=0, delivery_failures=None)` em `apps/worker/app/tasks/messages.py` (novo parâmetro no final, sem mudar a ordem dos existentes); `send_message_to_agents` em `apps/worker/app/clients/agents.py` devolvendo `delivery_failures` na resposta.

- [ ] **Step 1: Migration**

Criar `apps/api/alembic/versions/0007_message_delivery_status.py`:

```python
"""delivery_status em messages

Marca se uma mensagem de saída (agent/human) foi entregue ao WhatsApp com
sucesso — nullable porque só é significativo pra sender_type agent/human;
mensagens de contato e mensagens já existentes antes desta migration ficam
NULL, sem retroatividade.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("delivery_status", sa.String(), nullable=True))
    op.create_check_constraint(
        "delivery_status", "messages", "delivery_status IN ('sent', 'failed')"
    )


def downgrade() -> None:
    op.drop_constraint("delivery_status", "messages", type_="check")
    op.drop_column("messages", "delivery_status")
```

- [ ] **Step 2: Model**

Em `apps/api/app/models/message.py`, adicionar `CheckConstraint("delivery_status IN ('sent', 'failed')", name="delivery_status")` ao `__table_args__` (junto do já existente):

```python
    __table_args__ = (
        CheckConstraint("sender_type IN ('agent', 'human', 'contact')", name="sender_type"),
        CheckConstraint("delivery_status IN ('sent', 'failed')", name="delivery_status"),
        # Queries do painel de conversas.
        Index("ix_messages_tenant_id_created_at", "tenant_id", "created_at"),
    )
```

Adicionar a coluna nova, após `content`:

```python
    sender_type: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    delivery_status: Mapped[str | None] = mapped_column(String)
    media_url: Mapped[str | None] = mapped_column(String)
```

- [ ] **Step 3: Escrever os testes do `api` que falham**

Em `apps/api/tests/unit/test_conversations_routes.py`, trocar (dentro de `TestListMessages.test_lista_mensagens`):

```python
        message = SimpleNamespace(
            id=uuid.uuid4(),
            sender_type="contact",
            content="Olá",
            media_url=None,
            media_type=None,
            created_at=datetime.now(UTC),
        )
```

por:

```python
        message = SimpleNamespace(
            id=uuid.uuid4(),
            sender_type="contact",
            content="Olá",
            media_url=None,
            media_type=None,
            delivery_status=None,
            created_at=datetime.now(UTC),
        )
```

Em `TestSendMessage.test_envia_e_persiste_como_human`, trocar:

```python
        persisted = session.add.call_args.args[0]
        assert persisted.sender_type == "human"
        assert persisted.tenant_id == TENANT_ID
        session.commit.assert_awaited_once()
```

por:

```python
        persisted = session.add.call_args.args[0]
        assert persisted.sender_type == "human"
        assert persisted.tenant_id == TENANT_ID
        assert persisted.delivery_status == "sent"
        assert response.json()["delivery_status"] == "sent"
        session.commit.assert_awaited_once()
```

- [ ] **Step 4: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py -v`
Expected: FAIL — `test_lista_mensagens` com erro de validação do Pydantic (`MessageOut` exige `delivery_status`, ainda ausente no schema); `test_envia_e_persiste_como_human` com `AttributeError`/`AssertionError` (`Message` ainda não seta `delivery_status`).

- [ ] **Step 5: Schema**

Em `apps/api/app/schemas/conversations.py`, atualizar `MessageOut`:

```python
class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sender_type: Literal["agent", "human", "contact"]
    content: str
    media_url: str | None
    media_type: str | None
    delivery_status: Literal["sent", "failed"] | None
    created_at: datetime
```

- [ ] **Step 6: Rota do takeover humano**

Em `apps/api/app/api/v1/conversations.py`, dentro de `send_message`, trocar:

```python
    message = Message(
        conversation_id=conversation.id,
        tenant_id=ctx.tenant_id,
        sender_type="human",
        content=body.content,
    )
```

por:

```python
    message = Message(
        conversation_id=conversation.id,
        tenant_id=ctx.tenant_id,
        sender_type="human",
        content=body.content,
        delivery_status="sent",
    )
```

- [ ] **Step 7: Rodar e ver passar**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py -v`
Expected: PASS em todos.

- [ ] **Step 8: Rodar a suíte completa, migration e lint do `api`**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos PASS, ruff limpo.

Run (ajustar credenciais conforme seu Postgres local): `DATABASE_URL="postgresql+asyncpg://advoxs:changeme@localhost:5433/advoxs" REDIS_URL="redis://localhost:6379/0" JWT_SECRET="test" uv run alembic upgrade head`
Expected: aplica a `0007` sem erro.

- [ ] **Step 9: `apps/worker/app/tables.py`**

Adicionar `delivery_status` à definição Core da tabela `messages`, após `content`:

```python
messages = Table(
    "messages",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("conversation_id", Uuid),
    Column("tenant_id", Uuid),
    Column("sender_type", String),
    Column("content", Text),
    Column("delivery_status", String),
    Column("tokens_used", Integer),
    Column("credits_consumed", Numeric(12, 2)),
    Column("created_at", DateTime(timezone=True), server_default=text("now()")),
)
```

- [ ] **Step 10: `apps/worker/app/clients/agents.py`**

Trocar o retorno de `send_message_to_agents`:

```python
async def send_message_to_agents(
    http: httpx.AsyncClient,
    *,
    tenant_id: str,
    contact_phone_number: str,
    message: str,
    phone_number_id: str,
    access_token: str,
) -> dict | None:
    """Chama POST /messages do agents service.

    Retorna {"responses": [...], "tokens_used": N, "delivery_failures": [...]},
    ou None quando o agents devolve 202 (a mensagem foi agrupada pelo debounce
    numa execução já em andamento — as respostas virão pela execução que está
    rodando).
    """
    headers = {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}
    response = await http.post(
        "/messages",
        json={
            "tenant_id": tenant_id,
            "contact_phone_number": contact_phone_number,
            "message": message,
            "attachments": [],
            "phone_number_id": phone_number_id,
            "access_token": access_token,
        },
        headers=headers,
    )
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

- [ ] **Step 11: Escrever os testes novos que falham (worker, os dois arquivos)**

Criar `apps/worker/tests/unit/test_persist_agent_responses.py`:

```python
import uuid

from app.tasks import messages as messages_task

TENANT_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class FakeSession:
    def __init__(self):
        self.executed_values: list[dict] = []
        self.next_id = uuid.uuid4()

    async def execute(self, stmt):
        params = dict(stmt.compile().params)
        self.executed_values.append(params)
        if "sender_type" in params:
            return FakeResult(self.next_id)
        return FakeResult(None)


async def test_marca_delivery_status_sent_por_padrao() -> None:
    session = FakeSession()

    first_id = await messages_task._persist_agent_responses(
        session, TENANT_ID, CONVERSATION_ID, ["resposta 1", "resposta 2"], 100, 1
    )

    assert first_id == session.next_id
    inserted = [v for v in session.executed_values if "sender_type" in v]
    assert inserted[0]["delivery_status"] == "sent"
    assert inserted[1]["delivery_status"] == "sent"


async def test_marca_delivery_status_failed_pelo_indice() -> None:
    session = FakeSession()

    await messages_task._persist_agent_responses(
        session, TENANT_ID, CONVERSATION_ID, ["resposta 1", "resposta 2"], 100, 1, {1}
    )

    inserted = [v for v in session.executed_values if "sender_type" in v]
    assert inserted[0]["delivery_status"] == "sent"
    assert inserted[1]["delivery_status"] == "failed"
```

Adicionar também, ao final de `apps/worker/tests/unit/test_process_inbound_message.py`:

```python


async def test_delivery_failures_repassado_ao_persistir(patched) -> None:
    patched["send"].return_value = {
        "responses": ["resposta 1", "resposta 2"],
        "tokens_used": 100,
        "delivery_failures": [1],
    }

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    persist_args = patched["persist"].await_args.args
    assert persist_args[6] == {1}
```

- [ ] **Step 12: Rodar e ver falhar**

Run: `cd apps/worker && uv run pytest tests/unit/test_persist_agent_responses.py tests/unit/test_process_inbound_message.py -v`
Expected: FAIL em 3 testes:
- `test_marca_delivery_status_sent_por_padrao`: `KeyError: 'delivery_status'` (a chave ainda não existe no dict `values` inserido — a assinatura atual de `_persist_agent_responses` não grava essa coluna).
- `test_marca_delivery_status_failed_pelo_indice`: `TypeError: _persist_agent_responses() takes from 4 to 6 positional arguments but 7 were given` (o parâmetro `delivery_failures` ainda não existe na assinatura).
- `test_delivery_failures_repassado_ao_persistir`: `IndexError: tuple index out of range` (o call site em `process_inbound_message` ainda chama `_persist_agent_responses` com só 6 argumentos posicionais).

- [ ] **Step 13: `apps/worker/app/tasks/messages.py`**

Trocar a assinatura e o corpo de `_persist_agent_responses`:

```python
async def _persist_agent_responses(
    session: AsyncSession,
    tenant_id: str,
    conversation_id: str,
    responses: list[str],
    tokens_used: int = 0,
    credits: int = 0,
    delivery_failures: set[int] | None = None,
) -> uuid.UUID | None:
    """Insere as respostas do agente e retorna o id da primeira.

    O consumo da execução inteira (tokens/créditos) fica registrado na
    primeira mensagem — é a ela que o lançamento do ledger se vincula.
    `delivery_failures` marca, por índice, quais respostas falharam ao
    entregar ao WhatsApp — a cobrança acontece independente disso, porque o
    custo do LLM já ocorreu.
    """
    delivery_failures = delivery_failures or set()
    now = datetime.now(UTC)
    first_message_id: uuid.UUID | None = None
    for i, response in enumerate(responses):
        values: dict = {
            "conversation_id": uuid.UUID(conversation_id),
            "tenant_id": uuid.UUID(tenant_id),
            "sender_type": "agent",
            "content": response,
            "delivery_status": "failed" if i in delivery_failures else "sent",
            "created_at": now,
        }
        if i == 0:
            values["tokens_used"] = tokens_used or None
            values["credits_consumed"] = credits or None
        result = await session.execute(
            insert(tables.messages).values(**values).returning(tables.messages.c.id)
        )
        if i == 0:
            first_message_id = result.scalar_one()
    if responses:
        await session.execute(
            update(tables.conversations)
            .where(tables.conversations.c.id == uuid.UUID(conversation_id))
            .values(last_message_at=now)
        )
    return first_message_id
```

E, dentro de `process_inbound_message`, trocar:

```python
    responses = result["responses"]
    tokens_used = result.get("tokens_used", 0)
    # 1 crédito = N tokens, sempre arredondando pra cima — nunca cobra fração.
    credits = math.ceil(tokens_used / settings.credit_tokens_per_credit) if tokens_used else 0

    async with session_factory() as session:
        first_message_id = await _persist_agent_responses(
            session, tenant_id, conversation_id, responses, tokens_used, credits
        )
```

por:

```python
    responses = result["responses"]
    tokens_used = result.get("tokens_used", 0)
    delivery_failures = set(result.get("delivery_failures", []))
    # 1 crédito = N tokens, sempre arredondando pra cima — nunca cobra fração.
    credits = math.ceil(tokens_used / settings.credit_tokens_per_credit) if tokens_used else 0

    async with session_factory() as session:
        first_message_id = await _persist_agent_responses(
            session, tenant_id, conversation_id, responses, tokens_used, credits, delivery_failures
        )
```

- [ ] **Step 14: Rodar e confirmar que os 3 testes passam**

Run: `cd apps/worker && uv run pytest tests/unit/test_persist_agent_responses.py tests/unit/test_process_inbound_message.py -v`
Expected: PASS em todos, incluindo `test_marca_delivery_status_sent_por_padrao`, `test_marca_delivery_status_failed_pelo_indice` e `test_delivery_failures_repassado_ao_persistir` (os 3 do Step 11), e os testes antigos que checam `persist_args[4]`/`persist_args[5]` continuam válidos porque `delivery_failures` foi adicionado como o 7º argumento posicional, sem deslocar os existentes.

- [ ] **Step 15: Rodar a suíte completa e lint do `worker`**

Run: `cd apps/worker && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos PASS, ruff limpo.

- [ ] **Step 16: Commit**

```bash
git add apps/api/alembic/versions/0007_message_delivery_status.py apps/api/app/models/message.py apps/api/app/schemas/conversations.py apps/api/app/api/v1/conversations.py apps/api/tests/unit/test_conversations_routes.py apps/worker/app/tables.py apps/worker/app/clients/agents.py apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_process_inbound_message.py apps/worker/tests/unit/test_persist_agent_responses.py
git commit -m "feat(api,worker): persiste delivery_status por mensagem de saída"
```

---

### Task 5: `web` — badge "Não entregue"

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/components/ConversationThread.tsx`
- Modify: `apps/web/__tests__/ConversationThread.test.tsx`

**Interfaces:**
- Consumes: `MessageOut.delivery_status` (Task 4), exposto no JSON de `GET conversations/{id}/messages`.
- Produces: nenhuma interface nova pra outras tasks — é a ponta final da cadeia.

- [ ] **Step 1: Tipo `Message`**

Em `apps/web/src/lib/types.ts`, atualizar a interface `Message`:

```ts
export interface Message {
  id: string;
  sender_type: SenderType;
  content: string;
  media_url: string | null;
  media_type: string | null;
  delivery_status: "sent" | "failed" | null;
  created_at: string;
}
```

- [ ] **Step 2: Atualizar o fixture de mensagens do teste existente**

Em `apps/web/__tests__/ConversationThread.test.tsx`, o array `messages` (usado por vários testes) tem:

```tsx
const messages: Message[] = [
  {
    id: "m2",
    sender_type: "agent",
    content: "Posso ajudar com o condomínio.",
    media_url: null,
    media_type: null,
    created_at: new Date().toISOString(),
  },
  {
    id: "m1",
    sender_type: "contact",
    content: "Olá, tenho uma dúvida.",
    media_url: null,
    media_type: null,
    created_at: new Date().toISOString(),
  },
];
```

Trocar para (adicionando `delivery_status: null` em cada objeto):

```tsx
const messages: Message[] = [
  {
    id: "m2",
    sender_type: "agent",
    content: "Posso ajudar com o condomínio.",
    media_url: null,
    media_type: null,
    delivery_status: null,
    created_at: new Date().toISOString(),
  },
  {
    id: "m1",
    sender_type: "contact",
    content: "Olá, tenho uma dúvida.",
    media_url: null,
    media_type: null,
    delivery_status: null,
    created_at: new Date().toISOString(),
  },
];
```

- [ ] **Step 3: Escrever os testes do badge que falham**

Adicionar ao final do arquivo `apps/web/__tests__/ConversationThread.test.tsx` (dentro do `describe("ConversationThread", ...)`):

```tsx
  it("mostra o badge 'Não entregue' quando a mensagem falhou ao entregar", async () => {
    const failedMessages: Message[] = [
      {
        id: "m3",
        sender_type: "agent",
        content: "Resposta que não chegou ao WhatsApp.",
        media_url: null,
        media_type: null,
        delivery_status: "failed",
        created_at: new Date().toISOString(),
      },
    ];
    backendFetchMock.mockResolvedValue(jsonResponse(failedMessages));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Não entregue")).toBeInTheDocument();
    });
  });

  it("não mostra o badge quando a mensagem foi entregue", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse(messages));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Posso ajudar com o condomínio.")).toBeInTheDocument();
    });
    expect(screen.queryByText("Não entregue")).not.toBeInTheDocument();
  });
```

- [ ] **Step 4: Rodar e ver falhar**

Run: `cd apps/web && npx --yes pnpm@9 test -- ConversationThread`
Expected: o teste `"mostra o badge..."` FALHA (o badge ainda não existe no componente); os demais devem continuar passando.

- [ ] **Step 5: Implementar o badge**

Em `apps/web/src/components/ConversationThread.tsx`, trocar a função `MessageBubble`:

```tsx
function MessageBubble({ message }: { message: Message }) {
  const fromContact = message.sender_type === "contact";
  const fromHuman = message.sender_type === "human";

  return (
    <li className={`flex flex-col ${fromContact ? "items-start" : "items-end"}`}>
      <div
        className={`max-w-[72%] rounded-md px-3.5 py-2.5 text-sm leading-relaxed ${
          fromContact
            ? "border border-line bg-surface"
            : fromHuman
              ? "bg-brass-soft"
              : "bg-accent-soft"
        }`}
      >
        {!fromContact ? (
          <span
            className={`mb-0.5 block font-mono text-[10px] uppercase tracking-[0.14em] ${
              fromHuman ? "text-brass" : "text-accent"
            }`}
          >
            {fromHuman ? "Você" : "Agente"}
          </span>
        ) : null}
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
      </div>
      <time className="mt-1 font-mono text-[10px] text-muted">
        {formatMessageTime(message.created_at)}
      </time>
    </li>
  );
}
```

por:

```tsx
function MessageBubble({ message }: { message: Message }) {
  const fromContact = message.sender_type === "contact";
  const fromHuman = message.sender_type === "human";

  return (
    <li className={`flex flex-col ${fromContact ? "items-start" : "items-end"}`}>
      <div
        className={`max-w-[72%] rounded-md px-3.5 py-2.5 text-sm leading-relaxed ${
          fromContact
            ? "border border-line bg-surface"
            : fromHuman
              ? "bg-brass-soft"
              : "bg-accent-soft"
        }`}
      >
        {!fromContact ? (
          <span
            className={`mb-0.5 block font-mono text-[10px] uppercase tracking-[0.14em] ${
              fromHuman ? "text-brass" : "text-accent"
            }`}
          >
            {fromHuman ? "Você" : "Agente"}
          </span>
        ) : null}
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
      </div>
      <div className="mt-1 flex items-center gap-1.5">
        {message.delivery_status === "failed" ? (
          <span className="rounded-sm bg-danger/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-danger">
            Não entregue
          </span>
        ) : null}
        <time className="font-mono text-[10px] text-muted">
          {formatMessageTime(message.created_at)}
        </time>
      </div>
    </li>
  );
}
```

- [ ] **Step 6: Rodar e ver passar**

Run: `cd apps/web && npx --yes pnpm@9 test -- ConversationThread`
Expected: PASS em todos.

- [ ] **Step 7: Rodar toda a suíte, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde (nenhum outro arquivo de teste constrói objetos `Message` diretamente, então nenhuma outra quebra é esperada — confirme lendo a saída do `test` completo).

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/components/ConversationThread.tsx apps/web/__tests__/ConversationThread.test.tsx
git commit -m "feat(web): badge 'Não entregue' na mensagem que falhou ao entregar"
```

---

### Task 6: `worker` — última tentativa trata e vira a conversa pra humano

**Files:**
- Modify: `apps/worker/app/tasks/messages.py`
- Modify: `apps/worker/tests/unit/test_process_inbound_message.py`

**Interfaces:**
- Consumes: nada de outras tasks desta feature (independente de Task 4, mesmo tocando o mesmo arquivo em pontos diferentes).
- Produces: nenhuma interface nova pra outras tasks.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao final de `apps/worker/tests/unit/test_process_inbound_message.py`:

```python


async def test_esgotadas_tentativas_vira_conversa_pra_human(patched) -> None:
    patched["send"].side_effect = httpx.ConnectError("agents fora do ar")
    ctx = _ctx()
    ctx["job_try"] = 5

    await process_inbound_message(ctx, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    session = ctx["session_factory"].return_value.__aenter__.return_value
    session.execute.assert_awaited()
    session.commit.assert_awaited()
    patched["persist"].assert_not_awaited()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/worker && uv run pytest tests/unit/test_process_inbound_message.py -v -k esgotadas`
Expected: FAIL — o teste espera que `process_inbound_message` retorne normalmente (sem levantar `Retry`), mas o código atual sempre relança `Retry` em `httpx.HTTPError`, então o teste falha com `arq.worker.Retry` não capturado propagando pra fora do `await`.

- [ ] **Step 3: Implementar o tratamento de última tentativa**

Em `apps/worker/app/tasks/messages.py`, adicionar a constante (após `logger = logging.getLogger(__name__)`):

```python
# Na última tentativa, vira a conversa pra humano em vez de reagendar (o
# default de max_tries do Arq também é 5 — manter em sincronia, mesmo padrão
# já usado em apps/worker/app/tasks/knowledge_base.py).
MAX_TRIES = 5
```

Trocar o bloco `except httpx.HTTPError as exc:`:

```python
    except httpx.HTTPError as exc:
        # Erro transiente (rede, 5xx): reagenda com backoff crescente.
        logger.warning(
            "Falha ao chamar agents, reagendando | tenant=%s conversation=%s erro=%s",
            tenant_id,
            conversation_id,
            exc,
        )
        raise Retry(defer=ctx.get("job_try", 1) * 10)
```

por:

```python
    except httpx.HTTPError as exc:
        if ctx.get("job_try", 1) < MAX_TRIES:
            # Erro transiente (rede, 5xx): reagenda com backoff crescente.
            logger.warning(
                "Falha ao chamar agents, reagendando | tenant=%s conversation=%s erro=%s",
                tenant_id,
                conversation_id,
                exc,
            )
            raise Retry(defer=ctx.get("job_try", 1) * 10)
        # Última tentativa: o agente não conseguiu processar. Vira a conversa
        # pra humano (mesmo mecanismo do bloqueio por saldo esgotado) em vez
        # de deixar o job desaparecer em silêncio depois do TTL do resultado.
        logger.error(
            "Esgotadas as tentativas de chamar agents, virando conversa pra human | "
            "tenant=%s conversation=%s erro=%s",
            tenant_id,
            conversation_id,
            exc,
        )
        async with session_factory() as session:
            await session.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == uuid.UUID(conversation_id))
                .values(state="human")
            )
            await session.commit()
        return
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/worker && uv run pytest tests/unit/test_process_inbound_message.py -v`
Expected: PASS em todos, incluindo `test_http_error_raises_retry` (que usa `job_try=1` por padrão via `_ctx()`, então `1 < 5` continua verdadeiro e o `Retry` ainda é levantado normalmente).

- [ ] **Step 5: Rodar a suíte completa e lint**

Run: `cd apps/worker && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos PASS, ruff limpo.

- [ ] **Step 6: Commit**

```bash
git add apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_process_inbound_message.py
git commit -m "fix(worker): última tentativa de chamar agents vira a conversa pra humano"
```

---

### Task 7: `CLAUDE.md` e verificação local ponta a ponta

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Atualizar o CLAUDE.md**

Na seção "Pendências específicas do WhatsApp", trocar:

```markdown
### Pendências específicas do WhatsApp

- [ ] Definir se haverá suporte a mensagens template (contato proativo) ou só reativo (dentro da janela de 24h).
- [ ] Rate limits da Cloud API por número — throttling na fila de envio.
- [ ] Retry/dead-letter para falhas de envio.
```

por:

```markdown
### Pendências específicas do WhatsApp

- [ ] Definir se haverá suporte a mensagens template (contato proativo) ou só reativo (dentro da janela de 24h).
- [x] ~~Rate limits da Cloud API por número — throttling na fila de envio.~~ (feito — token bucket simples via Redis em `apps/agents/clients/ratelimit.py`, por `phone_number_id`, teto de espera de 5s; ver seção Agents Service).
- [x] ~~Retry/dead-letter para falhas de envio.~~ (feito, sem dead-letter dedicado — retry curto na Graph API (3 tentativas, só falha transitória) + `messages.delivery_status` (`sent`/`failed`) exposto no painel via badge "Não entregue"; falha ao **chamar** o `agents` na última tentativa vira a conversa pra `human` em vez de desaparecer em silêncio; ver seções Agents Service, Frontend/`/conversas` e Modelo de Dados).
```

Na seção "Agents Service", no bloco "O que já existe e funciona", o primeiro parágrafo (o que descreve `POST /messages`, o debounce e menciona `clients/whatsapp.py`) termina hoje em `... e exibição da tag do agente ativo (playground de admin).`. Localizar a frase `Retorna \`{responses, tokens_used, current_agent}\` ao chamador` dentro desse parágrafo e trocar por `Retorna \`{responses, tokens_used, current_agent, delivery_failures}\` ao chamador`. Depois, adicionar ao final desse mesmo parágrafo (antes da quebra de linha pro próximo item da lista):

```markdown
 O envio à Graph API tem retry curto (3 tentativas, só para falha transitória — timeout/conexão/5xx, nunca 4xx) e rate limiting defensivo por número (token bucket via Redis, `WHATSAPP_RATE_LIMIT_PER_SECOND`, default 10/s) — ambos em `clients/whatsapp.py`/`clients/ratelimit.py`. `delivery_failures` (índices das respostas que não foram entregues) é persistido pelo `worker` em `messages.delivery_status`.
```

Na seção "Painel de Conversas" (`/conversas`) do Frontend, no item que já descreve o switch de IA e o resumo, adicionar ao final da frase: ` Mensagens que falharam ao entregar (`delivery_status="failed"`) mostram um badge "Não entregue" na bolha.`

Na seção "Modelo de Dados" → `messages`, adicionar à lista de colunas: `- \`delivery_status\` (nullable — \`sent\`|\`failed\`; só significativo pra \`sender_type\` \`agent\`/\`human\`; \`NULL\` pra mensagens de contato e mensagens anteriores a esta feature)`.

- [ ] **Step 2: Build e verificação local**

```bash
docker compose up -d --build agents api worker web
docker compose exec api uv run alembic upgrade head
```

1. Criar uma conversa + mensagem de contato via `psql` (mesmo padrão já usado em features anteriores) pro tenant de seed, com um número WhatsApp conectado.
2. Zerar temporariamente `access_token_encrypted`/usar um token inválido no `whatsapp_numbers` do tenant de seed (ou usar um `phone_number_id` errado) — disparar `process_inbound_message` manualmente dentro do container `worker` (`uv run python3`, chamando a função direto, sem passar por Redis/Arq real, mesmo padrão já usado antes nesta sessão) e confirmar: a Graph API rejeita (401/400), o retry NÃO ajuda (4xx não é retried), a mensagem do agente é persistida com `delivery_status="failed"`, e os créditos ainda são debitados normalmente.
3. Restaurar o token/número válidos, repetir o mesmo teste manual e confirmar `delivery_status="sent"`.
4. No `/conversas` do `web`, confirmar visualmente que a mensagem com `delivery_status="failed"` mostra o badge "Não entregue" e a outra não.
5. Simular esgotamento de tentativas: chamar `process_inbound_message` manualmente com `ctx["job_try"] = 5` e uma URL de `agents` inválida (`AGENTS_SERVICE_URL` errada temporariamente, ou desligar o container `agents`) — confirmar que a função retorna sem levantar `Retry` e que `conversations.state` virou `human` no banco.
6. Restaurar `AGENTS_SERVICE_URL`/o container `agents` e o estado da conversa (`state='agent'`) ao final.
7. Limpar todos os dados de teste criados (conversa, mensagens, credit_transactions) e restaurar o saldo do tenant de seed.

Expected: todos os passos funcionam; o passo 2 é o mais importante — prova que a falha de entrega, antes silenciosa, agora fica visível e não bloqueia o débito de créditos indevidamente (o custo do LLM ocorreu, é cobrado, e a falha de canal fica registrada separadamente).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: confiabilidade de envio no WhatsApp documentada no CLAUDE.md"
```
