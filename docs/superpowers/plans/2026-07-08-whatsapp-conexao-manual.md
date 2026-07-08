# Conexão Manual de WhatsApp Business Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que cada escritório conecte seu próprio número de WhatsApp Business pelo painel (`/configuracoes/whatsapp`), sem inserção manual no banco — validando token/PIN contra a Graph API antes de persistir qualquer credencial.

**Architecture:** Novo router `app/api/v1/whatsapp.py` no `api` com 3 rotas (`connect`, `connection`, `disconnect`) que reaproveitam a tabela `whatsapp_numbers` (1:1 com tenant, já existente). Duas novas funções em `app/clients/whatsapp.py` chamam a Graph API (`GET /{phone_number_id}` para validar+obter o número, `POST /{phone_number_id}/register` com o PIN) antes de qualquer escrita no banco. No `web`, uma página nova espelha o padrão de `/conversas`/`/base-de-conhecimento`.

**Tech Stack:** FastAPI + SQLAlchemy async (api), httpx, Next.js 15 App Router (web), pytest + Vitest.

## Global Constraints

- Modelo 1:1 tenant:número mantido (constraint `unique` em `whatsapp_numbers.tenant_id`, sem mudança de schema).
- Reconexão (tenant já conectado envia o formulário de novo) substitui a linha existente — não bloqueia.
- `display_phone_number` obtido via `GET` na Graph API, nunca pedido no formulário.
- PIN nunca persistido — só passa pela request, nunca logado.
- Ordem de validação: `GET` de validação → `POST /register` com o PIN → só então persiste. Falha em qualquer chamada à Meta = nada é salvo.
- Erros: falha da Meta (token/PIN inválido) → `400` com a mensagem da Meta; falha de rede → `502` com mensagem genérica em pt-BR (log do erro real, nunca exposto ao cliente); `phone_number_id` de outro tenant → `409`.
- `graph_api_base_url`/`graph_api_version` já existem em `app/core/config.py` — não precisa de env nova.
- Mensagens/comentários em pt-BR com acentuação correta.
- Commits: Conventional Commits em pt-BR. Testes: `uv run pytest tests/unit` (api), `pnpm test` (web). Lint: `uv run ruff check .` (api).

---

### Task 1: `api` — client Graph API (validar token e registrar número)

**Files:**
- Modify: `apps/api/app/clients/whatsapp.py`
- Test: `apps/api/tests/unit/test_whatsapp_client.py` (novo)

**Interfaces:**
- Consumes: `settings.graph_api_base_url`, `settings.graph_api_version` (já existem em `app/core/config.py`).
- Produces: `class WhatsAppNetworkError(Exception)`, `class WhatsAppApiError(Exception)`; `async def fetch_display_phone_number(phone_number_id: str, access_token: str) -> str`; `async def register_number(phone_number_id: str, access_token: str, pin: str) -> None`.

- [ ] **Step 1: Escrever os testes que falham**

Criar `apps/api/tests/unit/test_whatsapp_client.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import app.clients.whatsapp as whatsapp_client
from app.clients.whatsapp import (
    WhatsAppApiError,
    WhatsAppNetworkError,
    fetch_display_phone_number,
    register_number,
)


def _mock_async_client(monkeypatch, response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.get.return_value = response
    client.post.return_value = response
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(whatsapp_client.httpx, "AsyncClient", MagicMock(return_value=cm))
    return client


def _response(status_code: int, json_body: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.is_error = status_code >= 400
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


class TestFetchDisplayPhoneNumber:
    async def test_sucesso_retorna_numero(self, monkeypatch) -> None:
        response = _response(200, {"display_phone_number": "+5511987654321"})
        client = _mock_async_client(monkeypatch, response)

        result = await fetch_display_phone_number("PNID", "token-claro")

        assert result == "+5511987654321"
        client.get.assert_awaited_once()
        _, kwargs = client.get.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer token-claro"
        assert kwargs["params"]["fields"] == "display_phone_number"

    async def test_erro_da_meta_levanta_whatsapp_api_error(self, monkeypatch) -> None:
        response = _response(400, {"error": {"message": "Token inválido"}})
        _mock_async_client(monkeypatch, response)

        with pytest.raises(WhatsAppApiError, match="Token inválido"):
            await fetch_display_phone_number("PNID", "token-claro")

    async def test_erro_sem_corpo_json_usa_mensagem_padrao(self, monkeypatch) -> None:
        response = _response(500, {})
        response.json.side_effect = ValueError("no json")
        _mock_async_client(monkeypatch, response)

        with pytest.raises(WhatsAppApiError, match="Não foi possível validar"):
            await fetch_display_phone_number("PNID", "token-claro")

    async def test_falha_de_rede_levanta_whatsapp_network_error(self, monkeypatch) -> None:
        client = AsyncMock()
        client.get.side_effect = httpx.ConnectError("down")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(whatsapp_client.httpx, "AsyncClient", MagicMock(return_value=cm))

        with pytest.raises(WhatsAppNetworkError):
            await fetch_display_phone_number("PNID", "token-claro")


class TestRegisterNumber:
    async def test_sucesso_nao_levanta_e_envia_payload_correto(self, monkeypatch) -> None:
        response = _response(200, {"success": True})
        client = _mock_async_client(monkeypatch, response)

        await register_number("PNID", "token-claro", "123456")

        client.post.assert_awaited_once()
        _, kwargs = client.post.call_args
        assert kwargs["json"] == {"messaging_product": "whatsapp", "pin": "123456"}
        assert kwargs["headers"]["Authorization"] == "Bearer token-claro"

    async def test_pin_incorreto_levanta_whatsapp_api_error(self, monkeypatch) -> None:
        response = _response(400, {"error": {"message": "PIN incorreto"}})
        _mock_async_client(monkeypatch, response)

        with pytest.raises(WhatsAppApiError, match="PIN incorreto"):
            await register_number("PNID", "token-claro", "123456")

    async def test_falha_de_rede_levanta_whatsapp_network_error(self, monkeypatch) -> None:
        client = AsyncMock()
        client.post.side_effect = httpx.ConnectError("down")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(whatsapp_client.httpx, "AsyncClient", MagicMock(return_value=cm))

        with pytest.raises(WhatsAppNetworkError):
            await register_number("PNID", "token-claro", "123456")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_whatsapp_client.py -v`
Expected: FAIL na coleta — `ImportError: cannot import name 'WhatsAppApiError' from 'app.clients.whatsapp'`.

- [ ] **Step 3: Implementar**

Substituir o conteúdo de `apps/api/app/clients/whatsapp.py` (mantém `WhatsAppSendError`/`send_text_message` intactos, adiciona o resto):

```python
"""Envio de mensagem e conexão de número pela WhatsApp Cloud API (Graph API da Meta).

Usado no takeover humano do painel de conversas — o envio do agente é feito
pelo próprio agents service. Credenciais por tenant, descriptografadas de
whatsapp_numbers na hora do envio.
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class WhatsAppSendError(Exception):
    pass


class WhatsAppNetworkError(Exception):
    """Falha de rede ao chamar a Graph API (timeout, conexão, DNS)."""


class WhatsAppApiError(Exception):
    """Graph API respondeu com erro (token inválido, PIN incorreto, etc.)."""


def _meta_error_message(response: httpx.Response, fallback: str) -> str:
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return fallback


async def send_text_message(phone_number_id: str, access_token: str, to: str, text: str) -> None:
    url = f"{settings.graph_api_base_url}/{settings.graph_api_version}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                url,
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


async def fetch_display_phone_number(phone_number_id: str, access_token: str) -> str:
    """Valida o token/phone_number_id contra a Meta e retorna o número formatado."""
    url = f"{settings.graph_api_base_url}/{settings.graph_api_version}/{phone_number_id}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                params={"fields": "display_phone_number"},
            )
    except httpx.HTTPError as exc:
        raise WhatsAppNetworkError(f"Falha de rede ao validar número: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Graph API (GET número) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise WhatsAppApiError(
            _meta_error_message(
                response, "Não foi possível validar o Phone Number ID/token com a Meta"
            )
        )
    return response.json()["display_phone_number"]


async def register_number(phone_number_id: str, access_token: str, pin: str) -> None:
    """Registra o número na Cloud API usando o PIN de 2 fatores do WhatsApp."""
    url = f"{settings.graph_api_base_url}/{settings.graph_api_version}/{phone_number_id}/register"
    payload = {"messaging_product": "whatsapp", "pin": pin}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise WhatsAppNetworkError(f"Falha de rede ao registrar número: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Graph API (register) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise WhatsAppApiError(
            _meta_error_message(
                response, "Não foi possível registrar o número na Meta — verifique o PIN"
            )
        )
```

- [ ] **Step 4: Rodar os testes e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Expected: todos os testes PASS (inclusive os pré-existentes), ruff sem erros.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/clients/whatsapp.py apps/api/tests/unit/test_whatsapp_client.py
git commit -m "feat(api): client Graph API para validar token e registrar número WhatsApp"
```

---

### Task 2: `api` — rotas de conexão (`connect`, `connection`, `disconnect`)

**Files:**
- Create: `apps/api/app/schemas/whatsapp_connection.py`
- Create: `apps/api/app/api/v1/whatsapp.py`
- Modify: `apps/api/app/api/v1/router.py`
- Test: `apps/api/tests/unit/test_whatsapp_connection_routes.py`

**Interfaces:**
- Consumes: `fetch_display_phone_number`, `register_number`, `WhatsAppApiError`, `WhatsAppNetworkError` da Task 1; `encrypt_access_token` de `app/core/crypto.py` (já existe); `WhatsAppNumber` de `app/models` (já existe: `tenant_id` unique, `phone_number_id` unique, `waba_id`, `display_phone_number`, `access_token_encrypted`, `status`, `connected_at`).
- Produces: `POST /api/v1/whatsapp/connect` (body `{phone_number_id, waba_id, access_token, pin}`) → `200` + `WhatsAppConnectionOut`; `GET /api/v1/whatsapp/connection` → `WhatsAppConnectionOut | null`; `POST /api/v1/whatsapp/disconnect` → `200` + `WhatsAppConnectionOut`. `WhatsAppConnectionOut = {display_phone_number: str (mascarado), status: "connected"|"disconnected", connected_at: datetime}`.

- [ ] **Step 1: Schemas**

Criar `apps/api/app/schemas/whatsapp_connection.py`:

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ConnectWhatsAppRequest(BaseModel):
    phone_number_id: str = Field(min_length=1)
    waba_id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    pin: str = Field(pattern=r"^\d{6}$")


class WhatsAppConnectionOut(BaseModel):
    display_phone_number: str
    status: Literal["connected", "disconnected"]
    connected_at: datetime
```

- [ ] **Step 2: Escrever os testes que falham**

Criar `apps/api/tests/unit/test_whatsapp_connection_routes.py`:

```python
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

import app.api.v1.whatsapp as whatsapp_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.whatsapp import WhatsAppApiError, WhatsAppNetworkError
from app.main import app

TENANT_ID = uuid.uuid4()

CONNECT_BODY = {
    "phone_number_id": "PNID",
    "waba_id": "WABA",
    "access_token": "token-claro",
    "pin": "123456",
}


def _number(status: str = "connected") -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id=TENANT_ID,
        phone_number_id="PNID-antigo",
        waba_id="WABA-antigo",
        display_phone_number="+5511987654321",
        access_token_encrypted="cifrado",
        status=status,
        connected_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    )


@pytest.fixture
def session():
    mock = AsyncMock()
    mock.add = MagicMock()

    async def fake_refresh(obj):
        if getattr(obj, "connected_at", None) is None:
            obj.connected_at = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)

    mock.refresh.side_effect = fake_refresh
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


@pytest.fixture
def graph_mocks(monkeypatch):
    mocks = {
        "fetch": AsyncMock(return_value="+5511987654321"),
        "register": AsyncMock(return_value=None),
        "encrypt": MagicMock(return_value="token-cifrado"),
    }
    monkeypatch.setattr(whatsapp_module, "fetch_display_phone_number", mocks["fetch"])
    monkeypatch.setattr(whatsapp_module, "register_number", mocks["register"])
    monkeypatch.setattr(whatsapp_module, "encrypt_access_token", mocks["encrypt"])
    return mocks


def test_connect_sem_token_retorna_401() -> None:
    response = TestClient(app).post("/api/v1/whatsapp/connect", json=CONNECT_BODY)
    assert response.status_code == 401


class TestConnect:
    def test_conexao_feliz_nova(self, client, session, graph_mocks) -> None:
        session.scalar.return_value = None

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "connected"
        assert body["display_phone_number"] == "+55 **** 4321"
        session.add.assert_called_once()
        graph_mocks["fetch"].assert_awaited_once_with("PNID", "token-claro")
        graph_mocks["register"].assert_awaited_once_with("PNID", "token-claro", "123456")

    def test_reconexao_substitui_linha_existente(self, client, session, graph_mocks) -> None:
        existing = _number(status="disconnected")
        session.scalar.return_value = existing

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 200
        assert existing.status == "connected"
        assert existing.phone_number_id == "PNID"
        assert existing.waba_id == "WABA"
        session.add.assert_not_called()

    def test_falha_no_get_retorna_400_sem_persistir(self, client, session, graph_mocks) -> None:
        graph_mocks["fetch"].side_effect = WhatsAppApiError("token inválido")

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 400
        assert response.json()["detail"] == "token inválido"
        graph_mocks["register"].assert_not_awaited()
        session.commit.assert_not_awaited()

    def test_falha_no_register_retorna_400_sem_persistir(self, client, session, graph_mocks) -> None:
        graph_mocks["register"].side_effect = WhatsAppApiError("PIN incorreto")

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 400
        session.commit.assert_not_awaited()

    def test_falha_de_rede_no_get_retorna_502(self, client, session, graph_mocks) -> None:
        graph_mocks["fetch"].side_effect = WhatsAppNetworkError("timeout")

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 502

    def test_numero_de_outro_tenant_retorna_409(self, client, session, graph_mocks) -> None:
        session.scalar.return_value = None
        session.commit.side_effect = IntegrityError("stmt", {}, Exception("unique"))

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 409

    def test_pin_invalido_retorna_422(self, client) -> None:
        body = {**CONNECT_BODY, "pin": "12a456"}

        response = client.post("/api/v1/whatsapp/connect", json=body)

        assert response.status_code == 422


class TestGetConnection:
    def test_sem_numero_conectado_retorna_null(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.get("/api/v1/whatsapp/connection")

        assert response.status_code == 200
        assert response.json() is None

    def test_numero_conectado_retorna_mascarado(self, client, session) -> None:
        session.scalar.return_value = _number()

        response = client.get("/api/v1/whatsapp/connection")

        assert response.status_code == 200
        body = response.json()
        assert body["display_phone_number"] == "+55 **** 4321"
        assert body["status"] == "connected"


class TestDisconnect:
    def test_desconecta_com_sucesso(self, client, session) -> None:
        existing = _number(status="connected")
        session.scalar.return_value = existing

        response = client.post("/api/v1/whatsapp/disconnect")

        assert response.status_code == 200
        assert existing.status == "disconnected"
        assert response.json()["status"] == "disconnected"

    def test_desconectar_sem_conexao_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post("/api/v1/whatsapp/disconnect")

        assert response.status_code == 404
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_whatsapp_connection_routes.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'app.api.v1.whatsapp'`.

- [ ] **Step 4: Router**

Criar `apps/api/app/api/v1/whatsapp.py`:

```python
"""Conexão manual do número de WhatsApp Business do escritório (1:1 com tenant).

O escritório faz o setup do lado da Meta (app, System User, token permanente,
verificação do número) e cola as credenciais aqui. Antes de persistir, valida
o token e registra o número na Cloud API — nada é salvo se a Meta rejeitar.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.whatsapp import (
    WhatsAppApiError,
    WhatsAppNetworkError,
    fetch_display_phone_number,
    register_number,
)
from app.core.crypto import encrypt_access_token
from app.models import WhatsAppNumber
from app.schemas.whatsapp_connection import ConnectWhatsAppRequest, WhatsAppConnectionOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

_GRAPH_ERROR_DETAIL = "Falha ao comunicar com a Meta — tente novamente em instantes"


def _mask_phone_number(value: str) -> str:
    """Mantém DDI (3 chars) e os 4 últimos dígitos visíveis; mascara o resto."""
    if len(value) <= 7:
        return value
    return f"{value[:3]} **** {value[-4:]}"


def _to_out(number: WhatsAppNumber) -> WhatsAppConnectionOut:
    return WhatsAppConnectionOut(
        display_phone_number=_mask_phone_number(number.display_phone_number),
        status=number.status,
        connected_at=number.connected_at,
    )


@router.post("/connect")
async def connect(
    body: ConnectWhatsAppRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> WhatsAppConnectionOut:
    try:
        display_phone_number = await fetch_display_phone_number(
            body.phone_number_id, body.access_token
        )
    except WhatsAppNetworkError as exc:
        logger.error("Falha de rede ao validar número | erro=%s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_GRAPH_ERROR_DETAIL)
    except WhatsAppApiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    try:
        await register_number(body.phone_number_id, body.access_token, body.pin)
    except WhatsAppNetworkError as exc:
        logger.error("Falha de rede ao registrar número | erro=%s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_GRAPH_ERROR_DETAIL)
    except WhatsAppApiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    existing = await session.scalar(
        select(WhatsAppNumber).where(WhatsAppNumber.tenant_id == ctx.tenant_id)
    )
    encrypted = encrypt_access_token(body.access_token)
    now = datetime.now(UTC)

    if existing is not None:
        existing.phone_number_id = body.phone_number_id
        existing.waba_id = body.waba_id
        existing.display_phone_number = display_phone_number
        existing.access_token_encrypted = encrypted
        existing.status = "connected"
        existing.connected_at = now
        number = existing
    else:
        number = WhatsAppNumber(
            tenant_id=ctx.tenant_id,
            phone_number_id=body.phone_number_id,
            waba_id=body.waba_id,
            display_phone_number=display_phone_number,
            access_token_encrypted=encrypted,
            status="connected",
        )
        session.add(number)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este número já está conectado a outro escritório",
        )
    await session.refresh(number)
    return _to_out(number)


@router.get("/connection")
async def get_connection(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> WhatsAppConnectionOut | None:
    number = await session.scalar(
        select(WhatsAppNumber).where(WhatsAppNumber.tenant_id == ctx.tenant_id)
    )
    if number is None:
        return None
    return _to_out(number)


@router.post("/disconnect")
async def disconnect(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> WhatsAppConnectionOut:
    number = await session.scalar(
        select(WhatsAppNumber).where(WhatsAppNumber.tenant_id == ctx.tenant_id)
    )
    if number is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nenhum número conectado")

    number.status = "disconnected"
    await session.commit()
    await session.refresh(number)
    return _to_out(number)
```

- [ ] **Step 5: Registrar no router principal**

Em `apps/api/app/api/v1/router.py`, adicionar o import e o include (junto aos demais):

```python
from app.api.v1.whatsapp import router as whatsapp_router
```

```python
api_router.include_router(whatsapp_router)
```

- [ ] **Step 6: Rodar os testes e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Expected: todos PASS, ruff limpo.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/schemas/whatsapp_connection.py apps/api/app/api/v1/whatsapp.py apps/api/app/api/v1/router.py apps/api/tests/unit/test_whatsapp_connection_routes.py
git commit -m "feat(api): rotas de conexão manual do WhatsApp Business (connect, connection, disconnect)"
```

---

### Task 3: `web` — página `/configuracoes/whatsapp`

**Files:**
- Modify: `apps/web/src/lib/backend.ts`
- Modify: `apps/web/src/middleware.ts`
- Modify: `apps/web/src/app/conversas/page.tsx`
- Modify: `apps/web/src/app/base-de-conhecimento/page.tsx`
- Create: `apps/web/src/components/WhatsAppConnectionPanel.tsx`
- Create: `apps/web/src/app/configuracoes/whatsapp/page.tsx`
- Test: `apps/web/__tests__/WhatsAppConnectionPanel.test.tsx`
- Test: `apps/web/__tests__/backend.test.ts`

**Interfaces:**
- Consumes: `backendFetch` de `@/lib/client-api` (já suporta JSON body — nenhuma mudança no proxy é necessária, `connect`/`disconnect` usam JSON puro, não multipart); rotas `whatsapp/connect`, `whatsapp/connection`, `whatsapp/disconnect` da Task 2; `logout` de `../conversas/actions`.
- Produces: rota `/configuracoes/whatsapp` protegida pelo middleware; componente `WhatsAppConnectionPanel` (sem props obrigatórias).

- [ ] **Step 1: Teste que falha (allowlist)**

Em `apps/web/__tests__/backend.test.ts`, adicionar dentro do `describe("isAllowedPath", ...)`:

```ts
  it("permite rotas de whatsapp", () => {
    expect(isAllowedPath(["whatsapp", "connection"])).toBe(true);
  });
```

Run: `cd apps/web && npx --yes pnpm@9 test -- backend`
Expected: FAIL — `"whatsapp"` não está na allowlist.

- [ ] **Step 2: Allowlist**

Em `apps/web/src/lib/backend.ts`:

```ts
const ALLOWED_PREFIXES = ["conversations", "knowledge-base", "whatsapp"];
```

Run: `cd apps/web && npx --yes pnpm@9 test -- backend` → PASS.

- [ ] **Step 3: Teste do componente**

Criar `apps/web/__tests__/WhatsAppConnectionPanel.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WhatsAppConnectionPanel } from "@/components/WhatsAppConnectionPanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedBackendFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedBackendFetch.mockReset();
});

describe("WhatsAppConnectionPanel", () => {
  it("mostra o formulário quando não há conexão", async () => {
    mockedBackendFetch.mockResolvedValue({ ok: true, json: async () => null });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByText("Phone Number ID")).toBeInTheDocument());
  });

  it("mostra o número mascarado e o status quando conectado", async () => {
    mockedBackendFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        display_phone_number: "+55 **** 4321",
        status: "connected",
        connected_at: "2026-07-08T12:00:00Z",
      }),
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByText("+55 **** 4321")).toBeInTheDocument());
    expect(screen.getByText(/conectado/i)).toBeInTheDocument();
  });

  it("mostra estado desconectado com botão de reconectar", async () => {
    mockedBackendFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        display_phone_number: "+55 **** 4321",
        status: "disconnected",
        connected_at: "2026-07-08T12:00:00Z",
      }),
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByText(/desconectado/i)).toBeInTheDocument());
    expect(screen.getByText("Reconectar")).toBeInTheDocument();
  });
});
```

Run: `cd apps/web && npx --yes pnpm@9 test -- WhatsAppConnectionPanel`
Expected: FAIL — componente não existe.

- [ ] **Step 4: Componente**

Criar `apps/web/src/components/WhatsAppConnectionPanel.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { backendFetch } from "@/lib/client-api";

type Connection = {
  display_phone_number: string;
  status: "connected" | "disconnected";
  connected_at: string;
};

type FormState = {
  phone_number_id: string;
  waba_id: string;
  access_token: string;
  pin: string;
};

const EMPTY_FORM: FormState = { phone_number_id: "", waba_id: "", access_token: "", pin: "" };

const STATUS_LABEL: Record<Connection["status"], string> = {
  connected: "conectado",
  disconnected: "desconectado",
};

const STATUS_CLASS: Record<Connection["status"], string> = {
  connected: "bg-accent-soft text-accent",
  disconnected: "bg-brass-soft text-brass",
};

export function WhatsAppConnectionPanel() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function load() {
    try {
      const response = await backendFetch("whatsapp/connection");
      if (response.ok) {
        setConnection(await response.json());
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
    setSubmitting(true);
    try {
      const response = await backendFetch("whatsapp/connect", {
        method: "POST",
        body: JSON.stringify(form),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(body?.detail ?? "Falha ao conectar — tente novamente.");
        return;
      }
      setConnection(body);
      setShowForm(false);
      setForm(EMPTY_FORM);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDisconnect() {
    if (!window.confirm("Desconectar o número de WhatsApp deste escritório?")) return;
    setFeedback(null);
    try {
      const response = await backendFetch("whatsapp/disconnect", { method: "POST" });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(body?.detail ?? "Falha ao desconectar — tente novamente.");
        return;
      }
      setConnection(body);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
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
        <h1 className="font-display text-xl font-semibold text-ink">WhatsApp Business</h1>
        <p className="text-sm text-muted">
          Conecte o número de WhatsApp Business do escritório para os agentes atenderem pelo
          canal.
        </p>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {connection && !showForm ? (
          <div className="max-w-md rounded border border-line bg-surface p-6">
            <div className="flex items-center justify-between">
              <p className="font-medium text-ink">{connection.display_phone_number}</p>
              <span
                className={`rounded-full px-3 py-1 font-mono text-[10px] uppercase tracking-[0.15em] ${STATUS_CLASS[connection.status]}`}
              >
                {STATUS_LABEL[connection.status]}
              </span>
            </div>
            <p className="mt-1 text-xs text-muted">
              Conectado em {new Date(connection.connected_at).toLocaleDateString("pt-BR")}
            </p>
            <div className="mt-4 flex gap-4">
              {connection.status === "connected" && (
                <button
                  type="button"
                  onClick={() => void handleDisconnect()}
                  className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
                >
                  Desconectar
                </button>
              )}
              <button
                type="button"
                onClick={() => setShowForm(true)}
                className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
              >
                {connection.status === "connected" ? "Trocar número" : "Reconectar"}
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="flex max-w-md flex-col gap-4">
            <label className="flex flex-col gap-1 text-sm text-ink">
              Phone Number ID
              <input
                required
                value={form.phone_number_id}
                onChange={(event) => setForm({ ...form, phone_number_id: event.target.value })}
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-ink">
              WhatsApp Business Account ID
              <input
                required
                value={form.waba_id}
                onChange={(event) => setForm({ ...form, waba_id: event.target.value })}
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-ink">
              Access Token
              <input
                required
                type="password"
                value={form.access_token}
                onChange={(event) => setForm({ ...form, access_token: event.target.value })}
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-ink">
              PIN (6 dígitos)
              <input
                required
                type="password"
                inputMode="numeric"
                maxLength={6}
                value={form.pin}
                onChange={(event) => setForm({ ...form, pin: event.target.value })}
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <div className="flex gap-4">
              <button
                type="submit"
                disabled={submitting}
                className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
              >
                {submitting ? "Conectando..." : "Conectar"}
              </button>
              {connection && (
                <button
                  type="button"
                  onClick={() => setShowForm(false)}
                  className="font-mono text-xs uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                >
                  Cancelar
                </button>
              )}
            </div>
          </form>
        )}
      </div>
    </main>
  );
}
```

(Se algum nome de token do Tailwind não existir — ex.: `accent-soft`, `brass-soft` —, confira os nomes reais em `tailwind.config.ts`/`globals.css` e ajuste; esses já são usados em `KnowledgeBasePanel.tsx`.)

- [ ] **Step 5: Página e navegação**

Criar `apps/web/src/app/configuracoes/whatsapp/page.tsx`:

```tsx
import Link from "next/link";

import { WhatsAppConnectionPanel } from "@/components/WhatsAppConnectionPanel";

import { logout } from "../../conversas/actions";

export default function ConfiguracoesWhatsAppPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <nav className="flex w-14 shrink-0 flex-col items-center justify-between bg-ink py-5">
        <div className="flex flex-col items-center gap-6">
          <span className="font-display text-2xl font-semibold text-ground" aria-label="Advoxs">
            A.
          </span>
          <Link
            href="/conversas"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Conversas
          </Link>
          <Link
            href="/base-de-conhecimento"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Base
          </Link>
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground [writing-mode:vertical-rl]">
            Config
          </span>
        </div>
        <form action={logout}>
          <button
            type="submit"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Sair
          </button>
        </form>
      </nav>
      <WhatsAppConnectionPanel />
    </div>
  );
}
```

Em `apps/web/src/app/conversas/page.tsx`, localizar este bloco exato (o link "Base" já existente):

```tsx
          <Link
            href="/base-de-conhecimento"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Base
          </Link>
        </div>
```

E substituir por (mantém o link "Base" e adiciona o novo link "Config" antes do `</div>` de fechamento):

```tsx
          <Link
            href="/base-de-conhecimento"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Base
          </Link>
          <Link
            href="/configuracoes/whatsapp"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Config
          </Link>
        </div>
```

Em `apps/web/src/app/base-de-conhecimento/page.tsx`, localizar este bloco exato (o span "Base" já existente):

```tsx
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground [writing-mode:vertical-rl]">
            Base
          </span>
        </div>
```

E substituir por (mantém o span "Base" e adiciona o novo link "Config" antes do `</div>` de fechamento):

```tsx
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground [writing-mode:vertical-rl]">
            Base
          </span>
          <Link
            href="/configuracoes/whatsapp"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Config
          </Link>
        </div>
```

- [ ] **Step 6: Middleware**

Em `apps/web/src/middleware.ts`:

```ts
export const config = {
  matcher: [
    "/",
    "/login",
    "/conversas/:path*",
    "/base-de-conhecimento/:path*",
    "/configuracoes/:path*",
  ],
};
```

- [ ] **Step 7: Rodar os testes, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde (inclusive os testes dos Steps 1 e 3), build gera a rota `/configuracoes/whatsapp`.

- [ ] **Step 8: Commit**

```bash
git add apps/web
git commit -m "feat(web): página de conexão do WhatsApp Business"
```

---

### Task 4: Atualizar `CLAUDE.md` e verificação local

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: tudo das tasks anteriores.

- [ ] **Step 1: Atualizar o CLAUDE.md**

Seguindo o estilo das seções existentes:

- Seção "Estado atual do repositório": `api` ganhou `/api/v1/whatsapp/{connect,connection,disconnect}`; `web` ganhou `/configuracoes/whatsapp`.
- Seção "Integração WhatsApp Business" (onboarding do número): substituir a menção a "Embedded Signup da Meta" pelo modelo manual implementado — o escritório faz o setup do lado da Meta (app, System User, token permanente, verificação do número) e conecta pelo painel; token/PIN validados contra a Graph API antes de persistir; PIN nunca armazenado.
- Seção "Pendências específicas do WhatsApp": remover/ajustar o item "Setup do app Meta como Tech Provider" (só era necessário para Embedded Signup — não se aplica a este modelo).
- Seção "Retrofit... bloqueia produção": marcar como resolvido "O que falta para exercitar de ponta a ponta é o Embedded Signup" — agora o número entra pelo painel, sem inserção manual.

- [ ] **Step 2: Verificação local (sem credenciais reais da Meta)**

Sem um token/PIN reais de um WhatsApp Business Account, a verificação e2e completa (conectar de verdade) não é possível nesta sessão. Validar o que é possível localmente:

```bash
docker compose up -d --build api web
docker compose logs -f api web
```

1. Login no `web` e acessar `/configuracoes/whatsapp` — deve mostrar o formulário vazio (nenhum número conectado no seed).
2. Enviar o formulário com credenciais inválidas (qualquer string) — deve retornar erro claro (a Graph API real vai rejeitar o token/phone_number_id inexistente com `400`, exercitando o path de erro de verdade).
3. Conferir nos logs do `api` que nada foi persistido (nenhuma linha nova em `whatsapp_numbers`) após a tentativa com credenciais inválidas.

Expected: formulário funcional, erro tratado sem quebrar a página, nada persistido em caso de erro.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: conexão manual de WhatsApp Business documentada no CLAUDE.md"
```
