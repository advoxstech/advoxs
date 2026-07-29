# Conexão de WhatsApp via Z-API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que um tenant conecte o WhatsApp via Z-API (provedor não-oficial, conexão por QR code, sem aprovação de negócio) como alternativa ao WhatsApp Business Cloud API oficial da Meta — sem duplicar a lógica de conversas/agentes/billing, que já é agnóstica de canal.

**Architecture:** Coluna discriminadora `provider` (`"meta"` | `"zapi"`) em `whatsapp_numbers`, mesmo padrão já usado em `tenant_billing_settings.billing_provider`. Webhook e cliente de envio ficam em arquivos separados por provedor; o resto do pipeline (persistência de `messages`, fila do Arq, execução do agente, billing) não muda.

**Tech Stack:** FastAPI + Python 3.12 (`apps/api`, `apps/worker`), FastAPI + Python 3.13 (`apps/agents`), Next.js 15 (`apps/web`), Alembic, httpx.

## Global Constraints

- 1 número por tenant, nunca os dois provedores ao mesmo tempo — mesmo invariante de hoje, só ganha um discriminador.
- `/webhooks/whatsapp` (Meta) e `/whatsapp/connect` continuam **100% intocados** — nunca arriscar quebrar callbacks já configurados em apps Meta de tenants reais em produção.
- Credenciais Z-API cifradas em repouso com o mesmo Fernet já usado pro token da Meta (`encrypt_access_token`/`decrypt_access_token`, `WHATSAPP_TOKEN_ENCRYPTION_KEY`) — nenhuma env nova de criptografia.
- Z-API não assina webhook (sem HMAC) — a segurança do endpoint novo é o segredo aleatório no próprio path da URL (`zapi_webhook_secret`), comparado em tempo constante (`hmac.compare_digest`).
- Cobrança do cliente final (billing gate determinístico) continua bloqueada pra tenants Z-API nesta v1 — ver Task 6.
- Fora de escopo (não implementar nesta entrega): troca de provedor com migração de estado, mídia rica da Z-API (botões/listas/áudio), fluxo de "passkey challenge" no lugar do QR code, provisionamento de instância em nome do tenant, mais de uma instância Z-API por tenant.

---

## Task 1: Modelo de dados — coluna `provider` + campos Z-API em `whatsapp_numbers`

**Files:**
- Create: `apps/api/alembic/versions/0024_whatsapp_zapi.py`
- Modify: `apps/api/app/models/whatsapp_number.py`

**Interfaces:**
- Produces: colunas `provider` (`String`, default `"meta"`), `zapi_instance_id` (nullable, `UNIQUE`), `zapi_instance_token_encrypted` (nullable), `zapi_client_token_encrypted` (nullable), `zapi_webhook_secret` (nullable) em `whatsapp_numbers`; `phone_number_id`/`waba_id`/`access_token_encrypted` passam a ser nullable.

- [ ] **Step 1: Escrever a migration**

```python
"""Adiciona suporte a Z-API como provedor alternativo de WhatsApp — coluna
discriminadora `provider` + campos zapi_* em whatsapp_numbers, mesmo padrão
já usado em tenant_billing_settings.billing_provider. Ver
docs/superpowers/specs/2026-07-29-whatsapp-zapi-design.md.

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-29
"""

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_numbers",
        sa.Column("provider", sa.String(), nullable=False, server_default="meta"),
    )
    op.alter_column("whatsapp_numbers", "phone_number_id", nullable=True)
    op.alter_column("whatsapp_numbers", "waba_id", nullable=True)
    op.alter_column("whatsapp_numbers", "access_token_encrypted", nullable=True)

    op.add_column("whatsapp_numbers", sa.Column("zapi_instance_id", sa.String(), nullable=True))
    op.create_unique_constraint(
        "uq_whatsapp_numbers_zapi_instance_id", "whatsapp_numbers", ["zapi_instance_id"]
    )
    op.add_column(
        "whatsapp_numbers", sa.Column("zapi_instance_token_encrypted", sa.Text(), nullable=True)
    )
    op.add_column(
        "whatsapp_numbers", sa.Column("zapi_client_token_encrypted", sa.Text(), nullable=True)
    )
    op.add_column("whatsapp_numbers", sa.Column("zapi_webhook_secret", sa.String(), nullable=True))

    op.create_check_constraint(
        "ck_whatsapp_numbers_provider_fields",
        "whatsapp_numbers",
        "(provider = 'meta' AND phone_number_id IS NOT NULL AND waba_id IS NOT NULL "
        "AND access_token_encrypted IS NOT NULL) "
        "OR (provider = 'zapi' AND zapi_instance_id IS NOT NULL "
        "AND zapi_instance_token_encrypted IS NOT NULL AND zapi_webhook_secret IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_whatsapp_numbers_provider_fields", "whatsapp_numbers", type_="check")
    op.drop_column("whatsapp_numbers", "zapi_webhook_secret")
    op.drop_column("whatsapp_numbers", "zapi_client_token_encrypted")
    op.drop_column("whatsapp_numbers", "zapi_instance_token_encrypted")
    op.drop_constraint("uq_whatsapp_numbers_zapi_instance_id", "whatsapp_numbers", type_="unique")
    op.drop_column("whatsapp_numbers", "zapi_instance_id")
    op.alter_column("whatsapp_numbers", "access_token_encrypted", nullable=False)
    op.alter_column("whatsapp_numbers", "waba_id", nullable=False)
    op.alter_column("whatsapp_numbers", "phone_number_id", nullable=False)
    op.drop_column("whatsapp_numbers", "provider")
```

- [ ] **Step 2: Rodar a migration**

Run: `cd apps/api && uv run alembic upgrade head`
Expected: aplica sem erro.

- [ ] **Step 3: Atualizar o model**

Editar `apps/api/app/models/whatsapp_number.py` — trocar o `__table_args__` e os campos:

```python
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WhatsAppNumber(Base):
    """Número WhatsApp conectado (tenant-scoped, 1:1 com tenant) — via Meta
    (oficial) ou Z-API (não-oficial, conexão por QR code, sem aprovação de
    negócio). `provider` discrimina qual bloco de colunas está preenchido."""

    __tablename__ = "whatsapp_numbers"
    __table_args__ = (
        CheckConstraint("status IN ('connected', 'disconnected')", name="status"),
        CheckConstraint(
            "(provider = 'meta' AND phone_number_id IS NOT NULL AND waba_id IS NOT NULL "
            "AND access_token_encrypted IS NOT NULL) "
            "OR (provider = 'zapi' AND zapi_instance_id IS NOT NULL "
            "AND zapi_instance_token_encrypted IS NOT NULL AND zapi_webhook_secret IS NOT NULL)",
            name="ck_whatsapp_numbers_provider_fields",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, unique=True
    )
    provider: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'meta'"))
    # Meta — nullable, só preenchido quando provider="meta".
    # Unique: é a chave de resolução do webhook (phone_number_id -> tenant);
    # dois tenants nunca compartilham o mesmo número.
    phone_number_id: Mapped[str | None] = mapped_column(String, unique=True)
    waba_id: Mapped[str | None] = mapped_column(String)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    # Z-API — nullable, só preenchido quando provider="zapi".
    # zapi_instance_id é a chave de resolução do webhook, equivalente ao
    # phone_number_id da Meta.
    zapi_instance_id: Mapped[str | None] = mapped_column(String, unique=True)
    zapi_instance_token_encrypted: Mapped[str | None] = mapped_column(Text)
    zapi_client_token_encrypted: Mapped[str | None] = mapped_column(Text)
    # Segredo nosso (não é credencial da Z-API) que compõe a URL do webhook —
    # única camada de autenticação do endpoint, já que a Z-API não assina o
    # payload como a Meta faz.
    zapi_webhook_secret: Mapped[str | None] = mapped_column(String)
    display_phone_number: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'connected'"))
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
```

Note: `display_phone_number` continua `NOT NULL` — pra Z-API, só é preenchido depois do QR pareado (a Z-API não devolve o número no momento da conexão de credenciais, só depois via `GET .../phone`); até lá a linha existe com `status="disconnected"` e a coluna populada com um placeholder (`"Aguardando pareamento"`, ver Task 3) — não pode ficar `NULL` sem alterar a constraint, e alterar essa constraint não vale a pena só por essa janela curta.

- [ ] **Step 4: Rodar a suíte completa e ruff**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: sem falhas novas (os testes existentes de `whatsapp` provavelmente já quebram aqui — ver Task 4, que os corrige; se quebrarem já nesta task, é esperado e será corrigido lá, não precisa investigar agora).

- [ ] **Step 5: Commit**

```bash
cd apps/api
git add alembic/versions/0024_whatsapp_zapi.py app/models/whatsapp_number.py
git commit -m "feat(api): coluna provider e campos Z-API em whatsapp_numbers"
```

---

## Task 2: Cliente Z-API (`apps/api`) — validação, QR code, dispositivo, webhook, disconnect

**Files:**
- Create: `apps/api/app/clients/zapi.py`
- Test: `apps/api/tests/unit/test_zapi_client.py`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces: `ZApiNetworkError`, `ZApiApiError` (exceptions); `check_zapi_status(instance_id, token, client_token) -> dict` (`{"connected": bool, ...}`); `configure_zapi_webhook(instance_id, token, client_token, webhook_url) -> None`; `fetch_zapi_qrcode(instance_id, token, client_token) -> str` (base64); `fetch_zapi_connected_phone(instance_id, token, client_token) -> str | None`; `disconnect_zapi_instance(instance_id, token, client_token) -> None`.

- [ ] **Step 1: CONFIRMAR ANTES DE CODIFICAR — formato exato de cada chamada**

Esta task depende de detalhes da API da Z-API que a spec já pesquisou em alto nível (`docs/superpowers/specs/2026-07-29-whatsapp-zapi-design.md`), mas o formato exato (headers vs. path pra cada endpoint específico) precisa ser reconfirmado — a própria pesquisa da spec encontrou documentação inconsistente entre endpoints (alguns exemplos mostram `instanceId`/`token` no path da URL, como em `send-text`; outros mostram os dois como headers HTTP, como em `status`/`qr-code-image`). Antes de escrever qualquer código: use `WebFetch` contra `https://developer.z-api.io/instance/status.md`, `https://developer.z-api.io/instance/qr-code-image.md`, `https://developer.z-api.io/instance/device.md`, `https://developer.z-api.io/webhooks/update-every-webhooks.md` e `https://developer.z-api.io/instance/disconnect.md` pra confirmar o formato real de cada um (path vs. header, método HTTP exato). Se a documentação continuar ambígua/inconsistente entre fontes, adote o padrão mais comum observado (`instanceId`/`token` no path da URL, como no `send-text` já confirmado) para todos os endpoints — é mais fácil de testar e consistente com o padrão já usado no restante do arquivo — e documente essa escolha num comentário no topo do arquivo novo.

- [ ] **Step 2: Escrever o teste que falha**

```python
import httpx
import pytest

from app.clients.zapi import (
    ZApiApiError,
    ZApiNetworkError,
    check_zapi_status,
    configure_zapi_webhook,
    disconnect_zapi_instance,
    fetch_zapi_connected_phone,
    fetch_zapi_qrcode,
)


class TestCheckZApiStatus:
    async def test_retorna_o_corpo_da_resposta(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"connected": False, "smartphoneConnected": False})
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMockReturning(response))

        result = await check_zapi_status("inst-1", "token-1", None)

        assert result == {"connected": False, "smartphoneConnected": False}

    async def test_erro_de_rede_levanta_zapi_network_error(self, monkeypatch) -> None:
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMockRaising(httpx.ConnectError("falhou")))

        with pytest.raises(ZApiNetworkError):
            await check_zapi_status("inst-1", "token-1", None)

    async def test_erro_http_levanta_zapi_api_error(self, monkeypatch) -> None:
        response = httpx.Response(401, text="Unauthorized")
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMockReturning(response))

        with pytest.raises(ZApiApiError):
            await check_zapi_status("inst-1", "token-1", None)


class TestConfigureZApiWebhook:
    async def test_chama_o_endpoint_de_webhook_com_a_url(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"value": True})
        post_mock = AsyncMockReturning(response)
        monkeypatch.setattr(httpx.AsyncClient, "post", post_mock)

        await configure_zapi_webhook("inst-1", "token-1", None, "https://exemplo.com/webhook/segredo")

        assert post_mock.await_count == 1


class TestFetchZApiQrcode:
    async def test_retorna_a_imagem_base64(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"value": "data:image/png;base64,AAAA"})
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMockReturning(response))

        result = await fetch_zapi_qrcode("inst-1", "token-1", None)

        assert "AAAA" in result


class TestFetchZApiConnectedPhone:
    async def test_retorna_o_telefone_quando_presente(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"phone": "5511999998888"})
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMockReturning(response))

        result = await fetch_zapi_connected_phone("inst-1", "token-1", None)

        assert result == "5511999998888"

    async def test_retorna_none_quando_ausente(self, monkeypatch) -> None:
        response = httpx.Response(200, json={})
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMockReturning(response))

        result = await fetch_zapi_connected_phone("inst-1", "token-1", None)

        assert result is None


class TestDisconnectZApiInstance:
    async def test_chama_o_endpoint_de_disconnect(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"value": True})
        post_mock = AsyncMockReturning(response)
        monkeypatch.setattr(httpx.AsyncClient, "post", post_mock)

        await disconnect_zapi_instance("inst-1", "token-1", None)

        assert post_mock.await_count == 1
```

Adicionar no topo do arquivo de teste os dois helpers usados acima (não existem na stdlib nem em `unittest.mock` prontos com esse nome exato — implementar como classes simples ou usar `unittest.mock.AsyncMock` diretamente com `return_value=response`/`side_effect=exc`; troque `AsyncMockReturning(response)` por `AsyncMock(return_value=response)` e `AsyncMockRaising(exc)` por `AsyncMock(side_effect=exc)` do próprio `unittest.mock` — os nomes acima são só didáticos, use o padrão real do projeto (ver `apps/api/tests/unit/test_whatsapp_client.py` se existir, ou o padrão de `httpx.AsyncClient` mockado já usado em `test_whatsapp_connection_routes.py`).

- [ ] **Step 3: Rodar o teste e confirmar que falha**

Run: `cd apps/api && uv run pytest tests/unit/test_zapi_client.py -v`
Expected: falha — `app.clients.zapi` não existe ainda.

- [ ] **Step 4: Implementar**

```python
"""Cliente HTTP da Z-API — provedor não-oficial de WhatsApp (conexão por QR
code, sem aprovação de negócio). Mesmo padrão de exceções de
app/clients/whatsapp.py (Meta): ZApiNetworkError pra falha de rede,
ZApiApiError pra erro retornado pela própria Z-API.

Forma das chamadas confirmada em app/clients/zapi.py — ver Step 1 desta
task pra fonte/data da confirmação."""

import logging

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.z-api.io"
_TIMEOUT = 15


class ZApiNetworkError(Exception):
    """Falha de rede ao chamar a Z-API (timeout, conexão, DNS)."""


class ZApiApiError(Exception):
    """A Z-API respondeu com erro (credenciais inválidas, instância não encontrada, etc.)."""


def _headers(client_token: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    if client_token:
        headers["Client-Token"] = client_token
    return headers


def _instance_url(instance_id: str, token: str, path: str) -> str:
    return f"{_BASE_URL}/instances/{instance_id}/token/{token}/{path}"


async def check_zapi_status(instance_id: str, token: str, client_token: str | None) -> dict:
    """Valida as credenciais e devolve o status de pareamento — usado tanto
    na validação inicial (conecta mesmo sem estar pareado ainda) quanto no
    polling de conexão (ver GET /whatsapp/zapi-status)."""
    url = _instance_url(instance_id, token, "status")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, headers=_headers(client_token))
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao consultar status na Z-API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Z-API (status) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError("Não foi possível validar as credenciais com a Z-API")
    return response.json()


async def configure_zapi_webhook(
    instance_id: str, token: str, client_token: str | None, webhook_url: str
) -> None:
    """Configura a URL de callback da instância via API — a Z-API entrega
    isso automaticamente, sem o tenant precisar colar nada num painel."""
    url = _instance_url(instance_id, token, "webhooks")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                url,
                headers=_headers(client_token),
                json={"value": webhook_url, "notifySentByMe": False},
            )
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao configurar webhook na Z-API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Z-API (webhooks) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError("Não foi possível configurar o webhook na Z-API")


async def fetch_zapi_qrcode(instance_id: str, token: str, client_token: str | None) -> str:
    """Devolve a imagem do QR code (base64, já pronta pra exibir num <img src=...>)."""
    url = _instance_url(instance_id, token, "qr-code-image")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, headers=_headers(client_token))
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao buscar QR code na Z-API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Z-API (qr-code-image) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError("Não foi possível obter o QR code da Z-API")
    return response.json()["value"]


async def fetch_zapi_connected_phone(
    instance_id: str, token: str, client_token: str | None
) -> str | None:
    """Número conectado à instância, só disponível depois do QR pareado —
    None se a Z-API ainda não devolver o campo (pareamento incompleto)."""
    url = _instance_url(instance_id, token, "device")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, headers=_headers(client_token))
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao buscar dispositivo na Z-API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Z-API (device) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError("Não foi possível consultar o dispositivo conectado na Z-API")
    return response.json().get("phone")


async def disconnect_zapi_instance(instance_id: str, token: str, client_token: str | None) -> None:
    url = _instance_url(instance_id, token, "disconnect")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, headers=_headers(client_token))
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao desconectar na Z-API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Z-API (disconnect) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError("Não foi possível desconectar a instância na Z-API")
```

Ajuste as URLs/headers conforme o que foi de fato confirmado no Step 1, se divergir do que está aqui.

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `cd apps/api && uv run pytest tests/unit/test_zapi_client.py -v`
Expected: todos passam.

- [ ] **Step 6: Rodar a suíte completa, ruff e ruff format**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`

- [ ] **Step 7: Commit**

```bash
cd apps/api
git add app/clients/zapi.py tests/unit/test_zapi_client.py
git commit -m "feat(api): cliente HTTP da Z-API (status, webhook, QR code, dispositivo, disconnect)"
```

---

## Task 3: Endpoints de conexão Z-API — `connect-zapi`, `zapi-status`, `zapi-qrcode`

**Files:**
- Modify: `apps/api/app/api/v1/whatsapp.py`
- Modify: `apps/api/app/schemas/whatsapp_connection.py`
- Test: `apps/api/tests/unit/test_whatsapp_zapi_routes.py`

**Interfaces:**
- Consumes: `check_zapi_status`, `configure_zapi_webhook`, `fetch_zapi_qrcode`, `fetch_zapi_connected_phone`, `ZApiNetworkError`, `ZApiApiError` (Task 2); `WhatsAppNumber.provider`/`zapi_*` (Task 1).
- Produces: `POST /api/v1/whatsapp/connect-zapi`, `GET /api/v1/whatsapp/zapi-status`, `GET /api/v1/whatsapp/zapi-qrcode`; schema `ConnectZApiRequest`; `WhatsAppConnectionOut` ganha `provider: Literal["meta", "zapi"]`.

- [ ] **Step 1: Escrever os testes que falham**

Ler `apps/api/tests/unit/test_whatsapp_connection_routes.py` por completo primeiro (padrão de fixture `client`/`session`, `TenantContext`) antes de escrever o arquivo novo:

```python
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.whatsapp as whatsapp_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.zapi import ZApiApiError, ZApiNetworkError
from app.main import app

TENANT_ID = uuid.uuid4()

CONNECT_ZAPI_BODY = {
    "instance_id": "inst-123",
    "instance_token": "token-claro",
    "client_token": "client-token-claro",
}


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


@pytest.fixture
def zapi_mocks(monkeypatch):
    mocks = {
        "check_status": AsyncMock(return_value={"connected": False}),
        "configure_webhook": AsyncMock(return_value=None),
        "encrypt": MagicMock(side_effect=lambda v: f"cifrado:{v}"),
    }
    monkeypatch.setattr(whatsapp_module, "check_zapi_status", mocks["check_status"])
    monkeypatch.setattr(whatsapp_module, "configure_zapi_webhook", mocks["configure_webhook"])
    monkeypatch.setattr(whatsapp_module, "encrypt_access_token", mocks["encrypt"])
    monkeypatch.setattr(whatsapp_module.settings, "api_public_url", "https://api.exemplo.com.br")
    return mocks


class TestConnectZApi:
    def test_conexao_feliz(self, client, session, zapi_mocks) -> None:
        session.scalar.return_value = None

        response = client.post("/api/v1/whatsapp/connect-zapi", json=CONNECT_ZAPI_BODY)

        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "zapi"
        assert body["status"] == "disconnected"
        session.add.assert_called_once()
        zapi_mocks["check_status"].assert_awaited_once_with(
            "inst-123", "token-claro", "client-token-claro"
        )
        zapi_mocks["configure_webhook"].assert_awaited_once()
        webhook_url_arg = zapi_mocks["configure_webhook"].await_args.args[3]
        assert webhook_url_arg.startswith("https://api.exemplo.com.br/api/v1/webhooks/zapi/")

    def test_credenciais_invalidas_retorna_400_sem_persistir(
        self, client, session, zapi_mocks
    ) -> None:
        zapi_mocks["check_status"].side_effect = ZApiApiError("credenciais inválidas")

        response = client.post("/api/v1/whatsapp/connect-zapi", json=CONNECT_ZAPI_BODY)

        assert response.status_code == 400
        session.commit.assert_not_awaited()

    def test_falha_de_rede_retorna_502(self, client, session, zapi_mocks) -> None:
        zapi_mocks["check_status"].side_effect = ZApiNetworkError("timeout")

        response = client.post("/api/v1/whatsapp/connect-zapi", json=CONNECT_ZAPI_BODY)

        assert response.status_code == 502

    def test_falha_ao_configurar_webhook_retorna_400_sem_persistir(
        self, client, session, zapi_mocks
    ) -> None:
        zapi_mocks["configure_webhook"].side_effect = ZApiApiError("falha ao configurar")

        response = client.post("/api/v1/whatsapp/connect-zapi", json=CONNECT_ZAPI_BODY)

        assert response.status_code == 400
        session.commit.assert_not_awaited()

    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).post("/api/v1/whatsapp/connect-zapi", json=CONNECT_ZAPI_BODY)
        assert response.status_code == 401
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_whatsapp_zapi_routes.py -v`
Expected: falha — rota não existe ainda.

- [ ] **Step 3: Adicionar o schema `ConnectZApiRequest` e o campo `provider`**

Em `apps/api/app/schemas/whatsapp_connection.py`:

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ConnectWhatsAppRequest(BaseModel):
    phone_number_id: str = Field(min_length=1)
    waba_id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    pin: str = Field(pattern=r"^\d{6}$")


class ConnectZApiRequest(BaseModel):
    instance_id: str = Field(min_length=1)
    instance_token: str = Field(min_length=1)
    client_token: str | None = None


class WhatsAppConnectionOut(BaseModel):
    provider: Literal["meta", "zapi"]
    display_phone_number: str
    status: Literal["connected", "disconnected"]
    connected_at: datetime


class WebhookConfigOut(BaseModel):
    callback_url: str
    verify_token: str
```

- [ ] **Step 4: Implementar as 3 rotas**

Em `apps/api/app/api/v1/whatsapp.py`, ajustar `_to_out` e adicionar as rotas novas:

```python
import secrets

from app.clients.zapi import (
    ZApiApiError,
    ZApiNetworkError,
    check_zapi_status,
    configure_zapi_webhook,
    fetch_zapi_connected_phone,
    fetch_zapi_qrcode,
)
from app.schemas.whatsapp_connection import ConnectZApiRequest  # junto do import já existente


def _to_out(number: WhatsAppNumber) -> WhatsAppConnectionOut:
    return WhatsAppConnectionOut(
        provider=number.provider,
        display_phone_number=_mask_phone_number(number.display_phone_number),
        status=number.status,
        connected_at=number.connected_at,
    )


@router.post("/connect-zapi")
async def connect_zapi(
    body: ConnectZApiRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> WhatsAppConnectionOut:
    try:
        await check_zapi_status(body.instance_id, body.instance_token, body.client_token)
    except ZApiNetworkError as exc:
        logger.error("Falha de rede ao validar credenciais Z-API | erro=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao comunicar com a Z-API — tente novamente em instantes",
        )
    except ZApiApiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    webhook_secret = secrets.token_urlsafe(32)
    base = settings.api_public_url.rstrip("/")
    webhook_url = f"{base}/api/v1/webhooks/zapi/{webhook_secret}"

    try:
        await configure_zapi_webhook(
            body.instance_id, body.instance_token, body.client_token, webhook_url
        )
    except ZApiNetworkError as exc:
        logger.error("Falha de rede ao configurar webhook Z-API | erro=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao comunicar com a Z-API — tente novamente em instantes",
        )
    except ZApiApiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    existing = await session.scalar(
        select(WhatsAppNumber).where(WhatsAppNumber.tenant_id == ctx.tenant_id)
    )
    encrypted_token = encrypt_access_token(body.instance_token)
    encrypted_client_token = (
        encrypt_access_token(body.client_token) if body.client_token else None
    )

    if existing is not None:
        existing.provider = "zapi"
        existing.zapi_instance_id = body.instance_id
        existing.zapi_instance_token_encrypted = encrypted_token
        existing.zapi_client_token_encrypted = encrypted_client_token
        existing.zapi_webhook_secret = webhook_secret
        existing.phone_number_id = None
        existing.waba_id = None
        existing.access_token_encrypted = None
        existing.display_phone_number = "Aguardando pareamento"
        existing.status = "disconnected"
        number = existing
    else:
        number = WhatsAppNumber(
            tenant_id=ctx.tenant_id,
            provider="zapi",
            zapi_instance_id=body.instance_id,
            zapi_instance_token_encrypted=encrypted_token,
            zapi_client_token_encrypted=encrypted_client_token,
            zapi_webhook_secret=webhook_secret,
            display_phone_number="Aguardando pareamento",
            status="disconnected",
        )
        session.add(number)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta instância já está conectada a outro escritório",
        )
    await session.refresh(number)
    return _to_out(number)


@router.get("/zapi-qrcode")
async def get_zapi_qrcode(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict:
    number = await session.scalar(
        select(WhatsAppNumber).where(
            WhatsAppNumber.tenant_id == ctx.tenant_id, WhatsAppNumber.provider == "zapi"
        )
    )
    if number is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nenhuma instância Z-API conectada")

    from app.core.crypto import decrypt_access_token

    token = decrypt_access_token(number.zapi_instance_token_encrypted)
    client_token = (
        decrypt_access_token(number.zapi_client_token_encrypted)
        if number.zapi_client_token_encrypted
        else None
    )
    try:
        qrcode_base64 = await fetch_zapi_qrcode(number.zapi_instance_id, token, client_token)
    except ZApiNetworkError as exc:
        logger.error("Falha de rede ao buscar QR code | erro=%s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_GRAPH_ERROR_DETAIL)
    except ZApiApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return {"qrcode_base64": qrcode_base64}


@router.get("/zapi-status")
async def get_zapi_status(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> WhatsAppConnectionOut | None:
    number = await session.scalar(
        select(WhatsAppNumber).where(
            WhatsAppNumber.tenant_id == ctx.tenant_id, WhatsAppNumber.provider == "zapi"
        )
    )
    if number is None:
        return None

    from app.core.crypto import decrypt_access_token

    token = decrypt_access_token(number.zapi_instance_token_encrypted)
    client_token = (
        decrypt_access_token(number.zapi_client_token_encrypted)
        if number.zapi_client_token_encrypted
        else None
    )
    try:
        live_status = await check_zapi_status(number.zapi_instance_id, token, client_token)
    except (ZApiNetworkError, ZApiApiError) as exc:
        logger.warning("Falha ao revalidar status Z-API (best-effort) | erro=%s", exc)
        return _to_out(number)

    if live_status.get("connected") and number.status != "connected":
        phone = await fetch_zapi_connected_phone(number.zapi_instance_id, token, client_token)
        if phone:
            number.display_phone_number = phone
        number.status = "connected"
        await session.commit()
        await session.refresh(number)

    return _to_out(number)
```

Import `secrets` no topo do arquivo, e `decrypt_access_token` (já pode ir no bloco de imports do topo em vez de inline — inline aqui só por brevidade do plano, no código real prefira o import no topo do arquivo, ao lado de `encrypt_access_token`).

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_whatsapp_zapi_routes.py -v`
Expected: todos passam.

- [ ] **Step 6: Rodar a suíte completa, ruff e ruff format**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: `test_whatsapp_connection_routes.py` provavelmente quebra aqui (`_to_out` agora exige `.provider`, que os `SimpleNamespace` de teste não têm) — **não corrija esses testes nesta task**, isso é o escopo exato da Task 4.

- [ ] **Step 7: Commit**

```bash
cd apps/api
git add app/api/v1/whatsapp.py app/schemas/whatsapp_connection.py tests/unit/test_whatsapp_zapi_routes.py
git commit -m "feat(api): endpoints de conexão Z-API (connect-zapi, zapi-status, zapi-qrcode)"
```

---

## Task 4: Generalizar `GET /connection` e `POST /disconnect` por provedor

**Files:**
- Modify: `apps/api/app/api/v1/whatsapp.py`
- Modify: `apps/api/tests/unit/test_whatsapp_connection_routes.py`

**Interfaces:**
- Consumes: `disconnect_zapi_instance` (Task 2); `_to_out` já ajustado (Task 3).
- Produces: `POST /whatsapp/disconnect` chama a Z-API quando `provider="zapi"` antes de marcar desconectado localmente.

- [ ] **Step 1: Corrigir a fixture `_number()` do arquivo de teste existente**

Em `apps/api/tests/unit/test_whatsapp_connection_routes.py`, a fixture `_number()` (usada por `TestGetConnection`/`TestDisconnect`/parte de `TestConnect`) precisa do campo `provider` — sem isso, `_to_out(number).provider` quebra com `AttributeError` desde a Task 3:

```python
def _number(status: str = "connected", provider: str = "meta") -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id=TENANT_ID,
        provider=provider,
        phone_number_id="PNID-antigo",
        waba_id="WABA-antigo",
        zapi_instance_id=None,
        zapi_instance_token_encrypted=None,
        zapi_client_token_encrypted=None,
        display_phone_number="+5511987654321",
        access_token_encrypted="cifrado",
        status=status,
        connected_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    )
```

- [ ] **Step 2: Rodar a suíte pra confirmar que os testes existentes voltam a passar**

Run: `cd apps/api && uv run pytest tests/unit/test_whatsapp_connection_routes.py -v`
Expected: todos passam (a mudança do Step 1 é só de fixture, nenhuma asserção nova ainda).

- [ ] **Step 3: Escrever o teste que falha (disconnect por provedor)**

Adicionar a `test_whatsapp_connection_routes.py`, dentro de `class TestDisconnect`:

```python
    def test_desconecta_instancia_zapi_chama_a_z_api(self, client, session, monkeypatch) -> None:
        import app.api.v1.whatsapp as whatsapp_module

        existing = _number(status="connected", provider="zapi")
        existing.zapi_instance_id = "inst-123"
        existing.zapi_instance_token_encrypted = "cifrado-token"
        session.scalar.return_value = existing
        disconnect_mock = AsyncMock(return_value=None)
        monkeypatch.setattr(whatsapp_module, "disconnect_zapi_instance", disconnect_mock)
        monkeypatch.setattr(
            whatsapp_module, "decrypt_access_token", MagicMock(return_value="token-claro")
        )

        response = client.post("/api/v1/whatsapp/disconnect")

        assert response.status_code == 200
        assert existing.status == "disconnected"
        disconnect_mock.assert_awaited_once_with("inst-123", "token-claro", None)

    def test_falha_ao_desconectar_na_z_api_ainda_desconecta_localmente(
        self, client, session, monkeypatch
    ) -> None:
        import app.api.v1.whatsapp as whatsapp_module
        from app.clients.zapi import ZApiApiError

        existing = _number(status="connected", provider="zapi")
        existing.zapi_instance_id = "inst-123"
        existing.zapi_instance_token_encrypted = "cifrado-token"
        session.scalar.return_value = existing
        monkeypatch.setattr(
            whatsapp_module,
            "disconnect_zapi_instance",
            AsyncMock(side_effect=ZApiApiError("já desconectado")),
        )
        monkeypatch.setattr(
            whatsapp_module, "decrypt_access_token", MagicMock(return_value="token-claro")
        )

        response = client.post("/api/v1/whatsapp/disconnect")

        assert response.status_code == 200
        assert existing.status == "disconnected"
```

- [ ] **Step 4: Rodar os testes e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_whatsapp_connection_routes.py -v -k zapi`
Expected: falha — `disconnect` ainda não ramifica por provedor.

- [ ] **Step 5: Implementar**

Em `apps/api/app/api/v1/whatsapp.py`, importar `disconnect_zapi_instance` e `ZApiApiError`/`ZApiNetworkError` (já importados na Task 3 se ficaram no topo do arquivo — confirme) e `decrypt_access_token`, e ajustar a rota:

```python
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

    if number.provider == "zapi":
        # Falha ao avisar a Z-API não deve travar o tenant desconectando
        # localmente — best-effort, mesmo espírito de outras integrações
        # externas neste código-base (ex: falha ao mandar confirmação de
        # pagamento não desfaz o crédito já commitado).
        token = decrypt_access_token(number.zapi_instance_token_encrypted)
        client_token = (
            decrypt_access_token(number.zapi_client_token_encrypted)
            if number.zapi_client_token_encrypted
            else None
        )
        try:
            await disconnect_zapi_instance(number.zapi_instance_id, token, client_token)
        except (ZApiNetworkError, ZApiApiError) as exc:
            logger.warning("Falha ao desconectar na Z-API (best-effort) | erro=%s", exc)

    number.status = "disconnected"
    await session.commit()
    await session.refresh(number)
    return _to_out(number)
```

- [ ] **Step 6: Rodar os testes e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_whatsapp_connection_routes.py -v`
Expected: todos passam.

- [ ] **Step 7: Rodar a suíte completa, ruff e ruff format**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`

- [ ] **Step 8: Commit**

```bash
cd apps/api
git add app/api/v1/whatsapp.py tests/unit/test_whatsapp_connection_routes.py
git commit -m "fix(api): corrige fixture de teste e desconecta na Z-API quando aplicável"
```

---

## Task 5: Webhook de entrada da Z-API

**Files:**
- Modify: `apps/api/app/schemas/whatsapp.py`
- Modify: `apps/api/app/services/whatsapp_inbound.py`
- Create: `apps/api/app/api/v1/webhooks/zapi.py`
- Modify: `apps/api/app/main.py` (registrar o router novo — confirmar o padrão exato lendo como os outros webhooks são registrados lá antes de editar)
- Test: `apps/api/tests/unit/test_zapi_webhook.py`
- Test: `apps/api/tests/unit/test_whatsapp_schemas.py`

**Interfaces:**
- Consumes: `WhatsAppNumber.provider`/`zapi_instance_id`/`zapi_webhook_secret` (Task 1).
- Produces: `POST /api/v1/webhooks/zapi/{webhook_secret}`; `extract_inbound_zapi_message(payload) -> InboundZApiMessage | None`; `handle_zapi_webhook(payload, webhook_secret, session, arq) -> dict`.

- [ ] **Step 1: Escrever o teste que falha (extração do schema)**

Ler `apps/api/tests/unit/test_whatsapp_schemas.py` por completo primeiro. Adicionar:

```python
from app.schemas.whatsapp import extract_inbound_zapi_message


def _zapi_payload(**overrides: object) -> dict:
    base = {
        "instanceId": "inst-123",
        "phone": "5511888888888",
        "messageId": "msg-abc",
        "fromMe": False,
        "text": {"message": "Olá, preciso de ajuda"},
    }
    base.update(overrides)
    return base


def test_extract_zapi_text_message() -> None:
    result = extract_inbound_zapi_message(_zapi_payload())

    assert result is not None
    assert result.zapi_instance_id == "inst-123"
    assert result.wa_message_id == "msg-abc"
    assert result.contact_phone_number == "5511888888888"
    assert result.content == "Olá, preciso de ajuda"


def test_extract_zapi_ignora_mensagem_from_me() -> None:
    result = extract_inbound_zapi_message(_zapi_payload(fromMe=True))

    assert result is None


def test_extract_zapi_ignora_sem_texto() -> None:
    result = extract_inbound_zapi_message(_zapi_payload(text=None))

    assert result is None


def test_extract_zapi_ignora_payload_sem_instance_id() -> None:
    payload = _zapi_payload()
    del payload["instanceId"]

    result = extract_inbound_zapi_message(payload)

    assert result is None
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd apps/api && uv run pytest tests/unit/test_whatsapp_schemas.py -v -k zapi`
Expected: falha — `extract_inbound_zapi_message` não existe.

- [ ] **Step 3: Implementar o schema de extração Z-API**

Em `apps/api/app/schemas/whatsapp.py`, adicionar ao final:

```python
class InboundZApiMessage(BaseModel):
    """Uma mensagem de contato extraída do webhook da Z-API, já normalizada."""

    zapi_instance_id: str
    wa_message_id: str
    contact_phone_number: str
    content: str


def extract_inbound_zapi_message(payload: dict) -> InboundZApiMessage | None:
    """Extrai a mensagem de um payload de webhook da Z-API — diferente da
    Meta, cada POST já é 1 mensagem só, sem lote. Ignora eco de mensagem
    enviada pelo próprio WhatsApp Web conectado (fromMe=true) e mensagens
    sem texto (mídia recebida via Z-API não é processada nesta v1)."""
    if payload.get("fromMe"):
        return None

    instance_id = payload.get("instanceId")
    message_id = payload.get("messageId")
    sender = payload.get("phone")
    text = payload.get("text") or {}
    content = text.get("message") if isinstance(text, dict) else None

    if not instance_id or not message_id or not sender or not content:
        return None

    return InboundZApiMessage(
        zapi_instance_id=instance_id,
        wa_message_id=message_id,
        contact_phone_number=sender,
        content=content,
    )
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `cd apps/api && uv run pytest tests/unit/test_whatsapp_schemas.py -v -k zapi`
Expected: todos passam.

- [ ] **Step 5: Refatorar `_persist_inbound_message` pra aceitar o `WhatsAppNumber` já resolvido**

Ler `apps/api/app/services/whatsapp_inbound.py` (já lido nesta sessão, reproduzido aqui pra referência do estado atual) — a função hoje resolve o `WhatsAppNumber` internamente por `phone_number_id`. Generalizar pra receber o número já resolvido, já que a Z-API resolve por `zapi_instance_id` em vez disso:

```python
"""Processamento de mensagens entrantes dos webhooks de WhatsApp (Meta e
Z-API). Fluxo: resolve o tenant (por phone_number_id ou zapi_instance_id,
conforme o provedor) -> upsert da conversa -> persiste a mensagem (dedup
por wa_message_id) -> enfileira o job no Arq. O worker decide entre agente
e humano (estado da conversa) e chama o agents service.
"""

import logging
from datetime import UTC, datetime

from arq.connections import ArqRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message, WhatsAppNumber
from app.schemas.whatsapp import (
    InboundWhatsAppMessage,
    InboundZApiMessage,
    extract_inbound_messages,
)

logger = logging.getLogger(__name__)


async def handle_meta_webhook(payload: dict, session: AsyncSession, arq: ArqRedis) -> dict:
    """Persiste as mensagens do payload e enfileira o processamento.

    Retorna um resumo ({"received": N}) — o corpo da resposta não importa
    para a Meta, só o status 200 rápido.
    """
    persisted: list[tuple[str, str, str]] = []  # (tenant_id, conversation_id, message_id)

    for inbound in extract_inbound_messages(payload):
        number = await session.scalar(
            select(WhatsAppNumber).where(
                WhatsAppNumber.phone_number_id == inbound.phone_number_id
            )
        )
        if number is None:
            logger.warning(
                "Webhook Meta para phone_number_id desconhecido: %s", inbound.phone_number_id
            )
            continue
        result = await _persist_inbound_message(
            number,
            contact_phone_number=inbound.contact_phone_number,
            wa_message_id=inbound.wa_message_id,
            content=inbound.content,
            media_id=inbound.media_id,
            media_type=inbound.media_type,
            session=session,
        )
        if result is not None:
            persisted.append(result)

    await session.commit()

    for tenant_id, conversation_id, message_id in persisted:
        await arq.enqueue_job(
            "process_inbound_message",
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )

    return {"received": len(persisted)}


async def handle_zapi_webhook(
    payload: dict, webhook_secret: str, session: AsyncSession, arq: ArqRedis
) -> dict:
    """Mesma forma de `handle_meta_webhook`, mas resolve o tenant por
    zapi_instance_id + confere o segredo do path (única autenticação do
    endpoint, já que a Z-API não assina o payload)."""
    from app.schemas.whatsapp import extract_inbound_zapi_message

    inbound = extract_inbound_zapi_message(payload)
    if inbound is None:
        return {"received": 0}

    number = await session.scalar(
        select(WhatsAppNumber).where(
            WhatsAppNumber.provider == "zapi",
            WhatsAppNumber.zapi_instance_id == inbound.zapi_instance_id,
        )
    )
    if number is None or number.zapi_webhook_secret != webhook_secret:
        logger.warning(
            "Webhook Z-API com segredo ou instância inválidos | instance=%s",
            inbound.zapi_instance_id,
        )
        return {"received": 0}

    result = await _persist_inbound_message(
        number,
        contact_phone_number=inbound.contact_phone_number,
        wa_message_id=inbound.wa_message_id,
        content=inbound.content,
        media_id=None,
        media_type=None,
        session=session,
    )
    if result is None:
        return {"received": 0}

    await session.commit()
    tenant_id, conversation_id, message_id = result
    await arq.enqueue_job(
        "process_inbound_message",
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    return {"received": 1}


async def _persist_inbound_message(
    number: WhatsAppNumber,
    *,
    contact_phone_number: str,
    wa_message_id: str,
    content: str,
    media_id: str | None,
    media_type: str | None,
    session: AsyncSession,
) -> tuple[str, str, str] | None:
    # Dedup: ambos os provedores podem reentregar webhook não confirmado.
    duplicate = await session.scalar(
        select(Message.id).where(Message.wa_message_id == wa_message_id)
    )
    if duplicate is not None:
        logger.info("Webhook duplicado ignorado (wamid=%s)", wa_message_id)
        return None

    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.tenant_id == number.tenant_id,
            Conversation.contact_phone_number == contact_phone_number,
        )
    )
    if conversation is None:
        conversation = Conversation(
            tenant_id=number.tenant_id,
            contact_phone_number=contact_phone_number,
        )
        session.add(conversation)
        await session.flush()

    conversation.last_message_at = datetime.now(UTC)

    message = Message(
        conversation_id=conversation.id,
        tenant_id=number.tenant_id,
        sender_type="contact",
        content=content,
        media_url=media_id,
        media_type=media_type,
        wa_message_id=wa_message_id,
    )
    session.add(message)
    await session.flush()

    return (str(number.tenant_id), str(conversation.id), str(message.id))
```

Mover o `from app.schemas.whatsapp import extract_inbound_zapi_message` pro bloco de imports do topo do arquivo em vez de inline dentro da função (feito inline aqui só por brevidade do plano).

- [ ] **Step 6: Atualizar os testes existentes de `test_whatsapp_webhook.py` (Meta) pra nova assinatura**

Como `_persist_inbound_message` mudou de assinatura, mas `handle_meta_webhook` (chamada pela rota) manteve a MESMA interface pública, os testes de `test_whatsapp_webhook.py` (que testam via `TestClient`, não chamam `_persist_inbound_message` diretamente) devem continuar passando sem alteração — rode pra confirmar:

Run: `cd apps/api && uv run pytest tests/unit/test_whatsapp_webhook.py -v`
Expected: todos passam sem edição. Se algum quebrar, é porque o refactor do Step 5 mudou comportamento observável por fora — investigue e corrija o Step 5, não o teste.

- [ ] **Step 7: Escrever o teste que falha (rota do webhook Z-API)**

Ler `apps/api/tests/unit/test_whatsapp_webhook.py` por completo primeiro (padrão de fixture `arq_pool`/`fake_session`/`client`, já reproduzido acima). Criar `apps/api/tests/unit/test_zapi_webhook.py`:

```python
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_system_session
from app.core.queue import get_arq_pool
from app.main import app

WEBHOOK_SECRET = "segredo-abc"
WEBHOOK_PATH = f"/api/v1/webhooks/zapi/{WEBHOOK_SECRET}"

TEXT_PAYLOAD = {
    "instanceId": "inst-123",
    "phone": "5511888888888",
    "messageId": "msg-abc",
    "fromMe": False,
    "text": {"message": "Olá"},
}


@pytest.fixture
def arq_pool():
    return AsyncMock()


@pytest.fixture
def fake_session():
    session = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def client(fake_session, arq_pool):
    async def override_session():
        yield fake_session

    async def override_arq():
        return arq_pool

    app.dependency_overrides[get_system_session] = override_session
    app.dependency_overrides[get_arq_pool] = override_arq
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestReceiveZApiWebhook:
    def test_persiste_e_enfileira_com_segredo_correto(
        self, client, fake_session, arq_pool
    ) -> None:
        tenant_id = uuid.uuid4()
        number = MagicMock(tenant_id=tenant_id, zapi_webhook_secret=WEBHOOK_SECRET)
        conversation = MagicMock(id=uuid.uuid4(), tenant_id=tenant_id)
        fake_session.scalar.side_effect = [number, None, conversation]

        response = client.post(WEBHOOK_PATH, json=TEXT_PAYLOAD)

        assert response.status_code == 200
        assert response.json() == {"received": 1}
        arq_pool.enqueue_job.assert_awaited_once()

    def test_segredo_errado_e_ignorado(self, client, fake_session, arq_pool) -> None:
        number = MagicMock(zapi_webhook_secret="outro-segredo-diferente")
        fake_session.scalar.side_effect = [number]

        response = client.post(WEBHOOK_PATH, json=TEXT_PAYLOAD)

        assert response.status_code == 200
        assert response.json() == {"received": 0}
        arq_pool.enqueue_job.assert_not_awaited()

    def test_instancia_desconhecida_e_ignorada(self, client, fake_session, arq_pool) -> None:
        fake_session.scalar.side_effect = [None]

        response = client.post(WEBHOOK_PATH, json=TEXT_PAYLOAD)

        assert response.status_code == 200
        assert response.json() == {"received": 0}
        arq_pool.enqueue_job.assert_not_awaited()

    def test_from_me_e_ignorado(self, client, arq_pool) -> None:
        payload = {**TEXT_PAYLOAD, "fromMe": True}

        response = client.post(WEBHOOK_PATH, json=payload)

        assert response.status_code == 200
        assert response.json() == {"received": 0}
        arq_pool.enqueue_job.assert_not_awaited()
```

- [ ] **Step 8: Rodar e confirmar que falha**

Run: `cd apps/api && uv run pytest tests/unit/test_zapi_webhook.py -v`
Expected: falha (404) — rota não existe.

- [ ] **Step 9: Implementar a rota**

Ler `apps/api/app/api/v1/webhooks/whatsapp.py` (já reproduzido nesta sessão) e `apps/api/app/main.py` pra confirmar o padrão exato de registro de router antes de criar o arquivo novo. Criar `apps/api/app/api/v1/webhooks/zapi.py`:

```python
import json

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_system_session
from app.core.queue import get_arq_pool
from app.services.whatsapp_inbound import handle_zapi_webhook

router = APIRouter(prefix="/webhooks/zapi", tags=["webhooks"])


@router.post("/{webhook_secret}")
async def receive_webhook(
    webhook_secret: str,
    request: Request,
    session: AsyncSession = Depends(get_system_session),
    arq: ArqRedis = Depends(get_arq_pool),
) -> dict:
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload inválido")

    return await handle_zapi_webhook(payload, webhook_secret, session, arq)
```

Registrar o router novo em `app/main.py`, seguindo exatamente o mesmo padrão já usado pra `webhooks/whatsapp` e `webhooks/stripe_connect` (confirme o import/`app.include_router(...)` exatos lendo o arquivo antes de editar).

- [ ] **Step 10: Rodar os testes e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_zapi_webhook.py -v`
Expected: todos passam.

- [ ] **Step 11: Rodar a suíte completa, ruff e ruff format**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`

- [ ] **Step 12: Commit**

```bash
cd apps/api
git add app/schemas/whatsapp.py app/services/whatsapp_inbound.py app/api/v1/webhooks/zapi.py app/main.py tests/unit/test_zapi_webhook.py tests/unit/test_whatsapp_schemas.py
git commit -m "feat(api): webhook de mensagem recebida via Z-API"
```

---

## Task 6: Bloquear cobrança do cliente final pra tenants Z-API

**Files:**
- Modify: `apps/api/app/api/v1/end_customer_billing.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_settings_routes.py`

**Interfaces:**
- Consumes: `WhatsAppNumber.provider` (Task 1).
- Produces: `PATCH /end-customer-billing/settings` recusa (`400`) `enabled=true` quando o tenant está conectado via Z-API.

**Por quê**: o billing gate determinístico (`apps/worker/app/billing_gate.py`) manda mensagens (texto e lista interativa) direto pela Graph API da Meta, sem passar pelo `agents` — não tem equivalente Z-API nesta entrega (ver Fora de escopo do plano). Sem essa guarda, um tenant Z-API com a cobrança habilitada teria o funil de venda de créditos pro cliente final quebrando silenciosamente (chamada à Meta com credenciais vazias) assim que o saldo de algum contato esgotasse.

- [ ] **Step 1: Escrever o teste que falha**

O arquivo de teste (`test_end_customer_billing_settings_routes.py`) usa um padrão simples: `_settings_row(**overrides)` monta o `SimpleNamespace` da linha de configuração, e `session.scalar.side_effect = [<retorno de _get_settings_row>, <retorno da checagem seguinte>, ...]` — cada chamada a `session.scalar` dentro da rota consome o próximo item da lista, na ordem em que o código as faz. Hoje, dentro de `if body.enabled is True:`, só existe 1 checagem (pacote ativo) além da leitura inicial da linha (`test_patch_habilitar_sem_pacote_ativo_retorna_400`/`test_patch_habilitar_com_pacote_ativo_funciona`, já lidos nesta sessão, usam `side_effect=[row, None-ou-uuid]`). Como o Step 3 desta task insere a checagem de provedor do WhatsApp **antes** da checagem de pacote, os testes novos precisam de 3 itens no `side_effect`, nesta ordem: linha de settings, provider do WhatsApp, resultado do pacote.

Adicionar a `test_end_customer_billing_settings_routes.py`:

```python
def test_patch_habilitar_com_whatsapp_zapi_retorna_400(client, session) -> None:
    session.scalar.side_effect = [
        _settings_row(stripe_secret_key_encrypted="cifrado"),  # _get_settings_row
        "zapi",  # provider do WhatsApp do tenant
    ]

    response = client.patch("/api/v1/end-customer-billing/settings", json={"enabled": True})

    assert response.status_code == 400
    assert "z-api" in response.json()["detail"].lower()


def test_patch_habilitar_com_whatsapp_meta_e_pacote_ativo_funciona(client, session) -> None:
    session.scalar.side_effect = [
        _settings_row(stripe_secret_key_encrypted="cifrado"),  # _get_settings_row
        "meta",  # provider do WhatsApp do tenant
        uuid.uuid4(),  # checagem de pacote ativo — existe pelo menos 1
    ]

    response = client.patch("/api/v1/end-customer-billing/settings", json={"enabled": True})

    assert response.status_code == 200
    assert response.json()["enabled"] is True


def test_patch_habilitar_sem_whatsapp_conectado_funciona(client, session) -> None:
    """Tenant que ainda não conectou nenhum WhatsApp (provider=None, sem
    linha em whatsapp_numbers) não deve ser bloqueado por essa guarda —
    ela existe pra impedir Z-API especificamente, não pra exigir Meta."""
    session.scalar.side_effect = [
        _settings_row(stripe_secret_key_encrypted="cifrado"),  # _get_settings_row
        None,  # nenhum whatsapp_numbers pra esse tenant
        uuid.uuid4(),  # checagem de pacote ativo — existe pelo menos 1
    ]

    response = client.patch("/api/v1/end-customer-billing/settings", json={"enabled": True})

    assert response.status_code == 200
```

⚠️ Os testes já existentes `test_patch_habilitar_sem_pacote_ativo_retorna_400`, `test_patch_habilitar_com_pacote_ativo_funciona`, `test_patch_habilitar_sem_tokens_per_credit_funciona`, `test_patch_habilitar_com_tudo_configurado_funciona`, `test_patch_habilitar_com_connect_sem_secret_key_funciona` (e qualquer outro que exercite `enabled=True` com sucesso ou com a checagem de pacote) vão QUEBRAR depois do Step 3, porque o `side_effect` deles só tem 2 itens e a rota vai consumir 3. Ajuste cada um desses testes existentes adicionando `"meta",` como o 2º item do `side_effect` (entre a linha de settings e a checagem de pacote/resultado seguinte) — não é escopo novo, é só manter os testes coerentes com o `session.scalar` a mais que a rota agora faz.

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_settings_routes.py -v`
Expected: os 3 testes novos falham (guarda não existe ainda); os testes existentes de `enabled=True` ainda passam nesta altura (a mudança de `side_effect` deles só é necessária depois do Step 3 — se preferir, ajuste-os já agora e confirme que falham por outro motivo até o Step 3 rodar; ambas ordens de execução chegam ao mesmo lugar).

- [ ] **Step 3: Implementar a guarda**

Em `apps/api/app/api/v1/end_customer_billing.py`, dentro do bloco `if body.enabled is True:` (mesmo bloco que já checa pacote ativo), adicionar a checagem de provedor do WhatsApp **antes** da checagem de pacote:

```python
    if body.enabled is True:
        whatsapp_provider = await session.scalar(
            select(WhatsAppNumber.provider).where(WhatsAppNumber.tenant_id == ctx.tenant_id)
        )
        if whatsapp_provider == "zapi":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cobrança do cliente final ainda não está disponível pra "
                    "escritórios conectados via Z-API"
                ),
            )
        has_active_package = await session.scalar(
            select(EndCustomerCreditPackage.id).where(
                EndCustomerCreditPackage.tenant_id == ctx.tenant_id,
                EndCustomerCreditPackage.active.is_(True),
            )
        )
        if has_active_package is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cadastre ao menos um pacote de créditos ativo antes de ativar a cobrança",
            )
```

Adicionar `WhatsAppNumber` ao import de `app.models` no topo do arquivo, se ainda não estiver lá.

- [ ] **Step 4: Ajustar os testes existentes que quebraram e rodar tudo de novo**

Adicione `"meta",` como o 2º item do `side_effect` em cada teste existente listado no Step 1 que exercita `enabled=True` além do ponto da guarda de pacote/connect (⚠️ atenção especial a `test_patch_habilitar_com_connect_sem_secret_key_funciona`/`test_patch_habilitar_com_connect_status_nao_active_retorna_400`, que já tem uma sequência própria de `side_effect` pras checagens de Connect — releia cada um antes de editar, a posição exata do item novo depende de quantas chamadas a `session.scalar` cada teste já espera antes da guarda de pacote).

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_settings_routes.py -v`
Expected: todos passam.

- [ ] **Step 5: Rodar a suíte completa, ruff e ruff format**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`

- [ ] **Step 6: Commit**

```bash
cd apps/api
git add app/api/v1/end_customer_billing.py tests/unit/test_end_customer_billing_settings_routes.py
git commit -m "fix(api): bloqueia cobranca do cliente final para tenants conectados via Z-API"
```

---

## Task 7: Worker — carregar e repassar credenciais Z-API

**Files:**
- Modify: `apps/worker/app/tables.py`
- Modify: `apps/worker/app/tasks/inbound_context.py`
- Modify: `apps/worker/app/tasks/messages.py`
- Modify: `apps/worker/app/clients/agents.py`
- Test: `apps/worker/tests/unit/test_messages.py` (ou o arquivo de teste real de `_load_context`/`process_inbound_message` — confirme o nome exato antes de editar)
- Test: `apps/worker/tests/unit/test_agents_client.py` (ou nome real do arquivo de teste de `send_message_to_agents` — confirme antes de editar)

**Interfaces:**
- Consumes: colunas `provider`/`zapi_instance_id`/`zapi_instance_token_encrypted`/`zapi_client_token_encrypted` (Task 1).
- Produces: `InboundContext` ganha `whatsapp_provider: str`, `zapi_instance_id: str | None`, `zapi_instance_token_encrypted: str | None`, `zapi_client_token_encrypted: str | None`; `send_message_to_agents` ganha `whatsapp_provider`, `zapi_instance_id`, `zapi_token`, `zapi_client_token` como kwargs opcionais.

- [ ] **Step 1: Atualizar a Core Table `whatsapp_numbers`**

Em `apps/worker/app/tables.py`, no bloco `whatsapp_numbers = Table(...)`, adicionar as colunas novas:

```python
whatsapp_numbers = Table(
    "whatsapp_numbers",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("provider", String),
    Column("phone_number_id", String),
    Column("access_token_encrypted", Text),
    Column("zapi_instance_id", String),
    Column("zapi_instance_token_encrypted", Text),
    Column("zapi_client_token_encrypted", Text),
    Column("status", String),
)
```

- [ ] **Step 2: Atualizar `InboundContext`**

Em `apps/worker/app/tasks/inbound_context.py`:

```python
@dataclass
class InboundContext:
    conversation_state: str
    contact_phone_number: str
    message_content: str
    whatsapp_provider: str
    phone_number_id: str | None
    access_token_encrypted: str | None
    zapi_instance_id: str | None
    zapi_instance_token_encrypted: str | None
    zapi_client_token_encrypted: str | None
    credit_balance: Decimal
    end_customer_billing_enabled: bool
    end_customer_balance: Decimal
    end_customer_packages: list[dict]
    agents: list[dict]
    human_last_seen_at: datetime | None = None
    billing_gate_step: str | None = None
    billing_gate_retries: int = 0
    billing_gate_checkout_url: str | None = None
    billing_gate_welcome_text: str | None = None
    end_customer_billing_exempt: bool = False
    end_customer_has_active_subscription: bool = False
```

- [ ] **Step 3: Escrever o teste que falha**

O arquivo real é `apps/worker/tests/unit/test_load_context.py`. Ele já tem uma fixture `_number()` (`SimpleNamespace(phone_number_id="PNID", access_token_encrypted="cifrado")`) usada por praticamente todo teste do arquivo via `_session_with(..., number=_number(), ...)`. Generalize essa fixture (mantendo compatível com todas as chamadas existentes, que não passam argumento nenhum) e adicione os 2 testes novos:

```python
def _number(provider="meta", **overrides):
    row = SimpleNamespace(
        provider=provider,
        phone_number_id="PNID",
        access_token_encrypted="cifrado",
        zapi_instance_id=None,
        zapi_instance_token_encrypted=None,
        zapi_client_token_encrypted=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


async def test_load_context_carrega_credenciais_zapi_quando_provider_e_zapi() -> None:
    numero_zapi = _number(
        provider="zapi",
        phone_number_id=None,
        access_token_encrypted=None,
        zapi_instance_id="inst-123",
        zapi_instance_token_encrypted="cifrado-token",
        zapi_client_token_encrypted="cifrado-client-token",
    )
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=numero_zapi,
        credit_balance=1000,
        billing_settings=None,
        balance=None,
        packages=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.whatsapp_provider == "zapi"
    assert context.zapi_instance_id == "inst-123"
    assert context.zapi_instance_token_encrypted == "cifrado-token"
    assert context.zapi_client_token_encrypted == "cifrado-client-token"
    assert context.phone_number_id is None
    assert context.access_token_encrypted is None


async def test_load_context_provider_meta_por_padrao() -> None:
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

    assert context.whatsapp_provider == "meta"
    assert context.zapi_instance_id is None
```

A troca de `_number()` pra aceitar `provider="meta"` como default é retrocompatível com toda chamada existente no arquivo (`number=_number()`, sem argumentos) — nenhum outro teste deste arquivo precisa mudar. `test_sem_agentes_retorna_lista_vazia` faz `assert session.execute.await_count == 7` — isso não muda, já que a query de `whatsapp_numbers` continua sendo 1 `execute` só, só com mais colunas selecionadas.

- [ ] **Step 4: Rodar o teste e confirmar que falha**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_load_context.py -v -k zapi` (⚠️ usar `python3 -m pytest`, não `uv run` — confirme rapidamente se o venv de `apps/worker` está saudável rodando `python3 -m pytest tests/unit -q` primeiro; se `uv run pytest` também funcionar normalmente aqui, pode usá-lo — só `apps/agents` tem o venv confirmadamente quebrado nesta sessão).

- [ ] **Step 5: Implementar em `_load_context`**

Em `apps/worker/app/tasks/messages.py`, ajustar a query de `whatsapp_numbers`:

```python
    number = (
        await session.execute(
            select(
                tables.whatsapp_numbers.c.provider,
                tables.whatsapp_numbers.c.phone_number_id,
                tables.whatsapp_numbers.c.access_token_encrypted,
                tables.whatsapp_numbers.c.zapi_instance_id,
                tables.whatsapp_numbers.c.zapi_instance_token_encrypted,
                tables.whatsapp_numbers.c.zapi_client_token_encrypted,
            ).where(
                tables.whatsapp_numbers.c.tenant_id == uuid.UUID(tenant_id),
                tables.whatsapp_numbers.c.status == "connected",
            )
        )
    ).one_or_none()
```

E no `return InboundContext(...)` no final da função:

```python
    return InboundContext(
        conversation_state=conversation.state,
        contact_phone_number=conversation.contact_phone_number,
        message_content=content,
        whatsapp_provider=number.provider,
        phone_number_id=number.phone_number_id,
        access_token_encrypted=number.access_token_encrypted,
        zapi_instance_id=number.zapi_instance_id,
        zapi_instance_token_encrypted=number.zapi_instance_token_encrypted,
        zapi_client_token_encrypted=number.zapi_client_token_encrypted,
        credit_balance=credit_balance,
        # ... resto dos campos já existentes, inalterados
    )
```

- [ ] **Step 6: Ajustar `send_message_to_agents` (o ponto de chamada em `messages.py`)**

Em `apps/worker/app/clients/agents.py`:

```python
async def send_message_to_agents(
    http: httpx.AsyncClient,
    *,
    tenant_id: str,
    contact_phone_number: str,
    message: str,
    whatsapp_provider: str = "meta",
    phone_number_id: str = "",
    access_token: str = "",
    zapi_instance_id: str = "",
    zapi_token: str = "",
    zapi_client_token: str = "",
    agents: list[dict] | None = None,
) -> dict | None:
    headers = {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}
    payload = {
        "tenant_id": tenant_id,
        "contact_phone_number": contact_phone_number,
        "message": message,
        "attachments": [],
        "whatsapp_provider": whatsapp_provider,
        "phone_number_id": phone_number_id,
        "access_token": access_token,
        "zapi_instance_id": zapi_instance_id,
        "zapi_token": zapi_token,
        "zapi_client_token": zapi_client_token,
        "agents": agents or [],
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

No ponto de chamada em `apps/worker/app/tasks/messages.py` (onde hoje `access_token = decrypt_access_token(inbound.access_token_encrypted)` roda incondicionalmente antes de `send_message_to_agents`), ramificar por provedor:

```python
    if inbound.whatsapp_provider == "zapi":
        zapi_token = decrypt_access_token(inbound.zapi_instance_token_encrypted)
        zapi_client_token = (
            decrypt_access_token(inbound.zapi_client_token_encrypted)
            if inbound.zapi_client_token_encrypted
            else ""
        )
        agents_kwargs = {
            "whatsapp_provider": "zapi",
            "zapi_instance_id": inbound.zapi_instance_id,
            "zapi_token": zapi_token,
            "zapi_client_token": zapi_client_token,
        }
    else:
        access_token = decrypt_access_token(inbound.access_token_encrypted)
        agents_kwargs = {
            "whatsapp_provider": "meta",
            "phone_number_id": inbound.phone_number_id,
            "access_token": access_token,
        }

    try:
        result = await send_message_to_agents(
            http,
            tenant_id=tenant_id,
            contact_phone_number=inbound.contact_phone_number,
            message=inbound.message_content,
            agents=inbound.agents,
            **agents_kwargs,
        )
```

(`decrypt_access_token` já cifra/decifra qualquer string, reaproveitado sem mudança pra Z-API — ver Task 1/spec.)

- [ ] **Step 7: Rodar os testes e confirmar que passam**

Run: `cd apps/worker && python3 -m pytest tests/unit -v -k zapi` (ou `uv run pytest`, conforme confirmado no Step 4).

- [ ] **Step 8: Rodar a suíte completa do worker**

Run: `cd apps/worker && python3 -m pytest tests/unit -q` (ou `uv run pytest tests/unit -q`)
Expected: sem falhas novas.

- [ ] **Step 9: Commit**

```bash
cd apps/worker
git add app/tables.py app/tasks/inbound_context.py app/tasks/messages.py app/clients/agents.py tests/unit/
git commit -m "feat(worker): carrega e repassa credenciais Z-API pro agents service"
```

---

## Task 8: `agents` — `ZApiClient` + roteamento por provedor

**Files:**
- Create: `apps/agents/clients/zapi.py`
- Modify: `apps/agents/api/routes.py`
- Test: `apps/agents/tests/unit/test_zapi_client.py`
- Test: `apps/agents/tests/unit/test_routes.py` (confirmar nome exato do arquivo de teste de rotas — pode ser outro; ler `apps/agents/tests/unit/` antes de editar)

**Interfaces:**
- Consumes: nada de tasks anteriores diretamente (serviço isolado, recebe tudo via `IncomingMessage`).
- Produces: `ZApiClient(instance_id, token, client_token)` com `send_text_message(to, text)`/`send_document_message(to, link, filename, caption)`, mesma interface e formato de retorno (`{"success", "data", "error"}`) de `WhatsAppClient`; `IncomingMessage` ganha `whatsapp_provider`/`zapi_instance_id`/`zapi_token`/`zapi_client_token`.

**Nota de design**: `ZApiClient` duplica deliberadamente o miolo de retry/rate-limit de `WhatsAppClient` (`apps/agents/clients/whatsapp.py`) em vez de extrair um helper compartilhado — evita mexer no arquivo já testado da Meta (`test_whatsapp_client.py` tem 11 testes cobrindo retry/rate-limit em detalhe) só pra economizar duplicação, e seque o mesmo princípio de isolamento já documentado no projeto pra clientes de canal.

- [ ] **Step 1: Escrever o teste que falha**

Ler `apps/agents/tests/unit/test_whatsapp_client.py` por completo primeiro (já reproduzido nesta sessão) — o teste de `ZApiClient` espelha a MESMA estrutura, trocando só o client testado e a URL/payload esperados:

```python
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

import clients.zapi as zapi_module
from clients.zapi import ZApiClient


@pytest.fixture
def client():
    return ZApiClient("inst-123", "token-do-tenant", None)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


@pytest.fixture(autouse=True)
def rate_limit_sempre_libera(monkeypatch):
    monkeypatch.setattr(zapi_module, "acquire_rate_limit_slot", AsyncMock(return_value=True))


class TestSendTextMessage:
    async def test_sucesso_monta_url_e_payload_corretos(self, client, monkeypatch) -> None:
        response = httpx.Response(200, json={"zaapId": "z1", "messageId": "m1", "id": "m1"})
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is True
        call = request_mock.await_args
        assert call.args[0] == "POST"
        assert "inst-123" in call.args[1]
        assert "token-do-tenant" in call.args[1]
        assert call.kwargs["json"] == {"phone": "5511999998888", "message": "oi"}

    async def test_client_token_vai_no_header_quando_presente(self, monkeypatch) -> None:
        client_com_token = ZApiClient("inst-123", "token-do-tenant", "client-token-abc")
        response = httpx.Response(200, json={"id": "m1"})
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        await client_com_token.send_text_message("5511999998888", "oi")

        assert request_mock.await_args.kwargs["headers"]["Client-Token"] == "client-token-abc"

    async def test_erro_4xx_nao_faz_retry(self, client, monkeypatch) -> None:
        response = httpx.Response(401, text="Unauthorized")
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is False
        assert request_mock.await_count == 1

    async def test_erro_5xx_faz_retry_e_se_recupera(self, client, monkeypatch) -> None:
        error_response = httpx.Response(503, text="indisponível")
        ok_response = httpx.Response(200, json={"id": "m2"})
        request_mock = AsyncMock(side_effect=[error_response, ok_response])
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is True
        assert request_mock.await_count == 2


class TestSendDocumentMessage:
    async def test_monta_payload_de_documento(self, client, monkeypatch) -> None:
        response = httpx.Response(200, json={"id": "m3"})
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_document_message(
            "5511999998888", "https://exemplo.com/doc.pdf", filename="contrato.pdf"
        )

        assert result["success"] is True
        assert request_mock.await_args.kwargs["json"]["document"] == "https://exemplo.com/doc.pdf"
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd apps/agents && python3 -m pytest tests/unit/test_zapi_client.py -v` (⚠️ `apps/agents` tem venv quebrado nesta sessão — usar `python3 -m pytest`/`ruff`, nunca `uv run`, neste app específico).
Expected: falha — `clients.zapi` não existe.

- [ ] **Step 3: CONFIRMAR ANTES DE CODIFICAR — endpoint exato de envio de documento**

O envio de texto (`POST .../send-text`, `{"phone", "message"}`) já foi confirmado na spec. O de documento (provavelmente `POST .../send-document/pdf` ou similar, formato de payload pode variar — não foi confirmado na pesquisa da spec) precisa ser checado agora: `WebFetch` contra `https://developer.z-api.io/llms.txt` procurando o endpoint de envio de documento/PDF antes de implementar `send_document_message`. Ajuste o Step 4 abaixo conforme o que for encontrado.

- [ ] **Step 4: Implementar**

```python
"""Cliente da Z-API (provedor não-oficial de WhatsApp, conexão por QR
code). Mesma interface de clients/whatsapp.py (Meta) — quem chama
(api/routes.py) não precisa saber qual dos dois está por trás. Retry/rate
limit duplicados deliberadamente de WhatsAppClient — ver docstring da
Task 8 do plano de implementação (mesmo princípio de isolamento já
documentado no projeto pra clientes de canal)."""

import asyncio
import httpx
import time
from loguru import logger

from clients.ratelimit import acquire_rate_limit_slot

_BASE_URL = "https://api.z-api.io"
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = [0.5, 1]


class ZApiClient:
    def __init__(self, instance_id: str, token: str, client_token: str | None = None):
        self._instance_id = instance_id
        self._token = token
        self._client_token = client_token
        self._base_url = f"{_BASE_URL}/instances/{instance_id}/token/{token}"
        self._client: httpx.AsyncClient | None = None
        logger.info("ZApiClient inicializado | instance_id={}", instance_id)

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

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._client_token:
            headers["Client-Token"] = self._client_token
        return headers

    async def _safe_request(self, method: str, url: str, **kwargs):
        client = self._get_client()
        last_error: dict = {"success": False, "data": None, "error": "Erro desconhecido"}

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            acquired = await acquire_rate_limit_slot(self._instance_id)
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
                logger.info(
                    "Executando requisição à Z-API | method={} url={} tentativa={}",
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
                logger.error("Timeout ao acessar Z-API | method={} url={} tentativa={}", method, url, attempt)
                last_error = {"success": False, "data": None, "error": "Timeout ao acessar Z-API"}
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                return last_error

            except httpx.ConnectError as e:
                logger.error(
                    "Erro de conexão com Z-API | method={} url={} error={} tentativa={}",
                    method, url, e, attempt,
                )
                last_error = {"success": False, "data": None, "error": f"Erro de conexão: {e}"}
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                return last_error

            except httpx.RequestError as e:
                logger.error("Erro de requisição à Z-API | method={} url={} error={}", method, url, e)
                return {"success": False, "data": None, "error": f"Erro de requisição: {e}"}

            except Exception as e:
                logger.exception("Erro inesperado ao acessar Z-API | method={} url={}", method, url)
                return {"success": False, "data": None, "error": f"Erro inesperado: {e}"}

        return last_error

    async def send_text_message(self, to: str, text: str):
        url = f"{self._base_url}/send-text"
        logger.info("Enviando mensagem de texto via Z-API | to={}", to)
        payload = {"phone": to, "message": text}
        return await self._safe_request("POST", url, headers=self._headers(), json=payload)

    async def send_document_message(
        self, to: str, link: str, filename: str | None = None, caption: str | None = None
    ):
        # Ajustar endpoint/payload conforme confirmado no Step 3 desta task
        # — placeholder abaixo assume um formato análogo ao send-text, a
        # confirmar contra a doc real antes de considerar esta task pronta.
        url = f"{self._base_url}/send-document/pdf"
        logger.info("Enviando documento via Z-API | to={} link={}", to, link)
        payload: dict = {"phone": to, "document": link}
        if filename:
            payload["fileName"] = filename
        if caption:
            payload["caption"] = caption
        return await self._safe_request("POST", url, headers=self._headers(), json=payload)
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `cd apps/agents && python3 -m pytest tests/unit/test_zapi_client.py -v`
Expected: todos passam (ajustar o teste `TestSendDocumentMessage` se o formato real, confirmado no Step 3, divergir do assumido acima).

- [ ] **Step 6: Roteamento por provedor em `api/routes.py`**

Em `apps/agents/api/routes.py`, ajustar `IncomingMessage`:

```python
class IncomingMessage(BaseModel):
    tenant_id: str
    contact_phone_number: str
    message: str = ""
    attachments: list = Field(default_factory=list)
    whatsapp_provider: str = "meta"
    phone_number_id: str = ""
    access_token: str = ""
    zapi_instance_id: str = ""
    zapi_token: str = ""
    zapi_client_token: str = ""
    send_to_whatsapp: bool = True
    agents: list[dict] = Field(default_factory=list)
```

E na rota `receive`, trocar o bloco de envio:

```python
        delivery_failures: list[int] = []
        if body.send_to_whatsapp:
            logger.info(
                "Enviando {} resposta(s) via WhatsApp | thread_id={} provider={}",
                len(response),
                thread_id,
                body.whatsapp_provider,
            )
            if body.whatsapp_provider == "zapi":
                whatsapp_client_cm = ZApiClient(
                    body.zapi_instance_id, body.zapi_token, body.zapi_client_token or None
                )
            else:
                whatsapp_client_cm = WhatsAppClient(body.phone_number_id, body.access_token)

            async with whatsapp_client_cm as client:
                for i, msg in enumerate(response):
                    result = await client.send_text_message(
                        body.contact_phone_number, msg
                    )
                    if not result.get("success"):
                        logger.warning(
                            "Falha ao entregar mensagem via WhatsApp | thread_id={} índice={} erro={}",
                            thread_id,
                            i,
                            result.get("error"),
                        )
                        delivery_failures.append(i)
        else:
            logger.info(
                "send_to_whatsapp=False — envio pulado | thread_id={}", thread_id
            )
```

Adicionar `from clients.zapi import ZApiClient` ao import no topo do arquivo.

- [ ] **Step 7: Escrever/ajustar teste de roteamento em `test_routes.py`**

O arquivo real é `apps/agents/tests/unit/test_routes.py`. Ele já tem um helper `_mock_whatsapp_client(monkeypatch)` que faz `monkeypatch.setattr(routes, "WhatsAppClient", cls)` — o teste novo espelha exatamente esse padrão, trocando pra `ZApiClient`:

```python
def _mock_zapi_client(monkeypatch):
    instance = MagicMock()
    instance.send_text_message = AsyncMock(return_value={"success": True})
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    cls = MagicMock(return_value=instance)
    monkeypatch.setattr(routes, "ZApiClient", cls)
    return cls, instance


def test_whatsapp_provider_zapi_usa_zapi_client(client, monkeypatch):
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(
        return_value=(
            ["resposta 1"],
            {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            None,
        )
    )
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    zapi_cls, zapi_instance = _mock_zapi_client(monkeypatch)

    payload = {
        **PAYLOAD,
        "phone_number_id": "",
        "access_token": "",
        "whatsapp_provider": "zapi",
        "zapi_instance_id": "inst-123",
        "zapi_token": "token-zapi",
        "zapi_client_token": "client-token-zapi",
    }
    response = client.post("/messages", json=payload)

    assert response.status_code == 200
    zapi_cls.assert_called_once_with("inst-123", "token-zapi", "client-token-zapi")
    zapi_instance.send_text_message.assert_awaited_once_with("5511999999999", "resposta 1")


def test_whatsapp_provider_zapi_sem_client_token_passa_none(client, monkeypatch):
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(
        return_value=(["resposta 1"], {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}, None)
    )
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    zapi_cls, _ = _mock_zapi_client(monkeypatch)

    payload = {
        **PAYLOAD,
        "phone_number_id": "",
        "access_token": "",
        "whatsapp_provider": "zapi",
        "zapi_instance_id": "inst-123",
        "zapi_token": "token-zapi",
    }
    client.post("/messages", json=payload)

    zapi_cls.assert_called_once_with("inst-123", "token-zapi", None)


def test_whatsapp_provider_meta_padrao_continua_usando_whatsapp_client(client, monkeypatch):
    """Regressão: nenhum campo novo no payload não pode mudar o comportamento
    já existente pra chamadores antigos (worker antes do deploy, por exemplo)."""
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(
        return_value=(["resposta 1"], {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}, None)
    )
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    wa_cls, wa_instance = _mock_whatsapp_client(monkeypatch)

    response = client.post("/messages", json=PAYLOAD)

    assert response.status_code == 200
    wa_cls.assert_called_once_with("111222333", "token-do-tenant")
    wa_instance.send_text_message.assert_awaited_once_with("5511999999999", "resposta 1")
```

- [ ] **Step 8: Rodar a suíte completa do `agents`**

Run: `cd apps/agents && python3 -m pytest tests/unit -q && python3 -m ruff check .`
Expected: sem falhas novas (⚠️ este projeto já tinha 32 erros pré-existentes de ruff documentados no CLAUDE.md — não se preocupe em zerá-los, só não introduza novos).

- [ ] **Step 9: Commit**

```bash
cd apps/agents
git add clients/zapi.py api/routes.py tests/unit/
git commit -m "feat(agents): ZApiClient e roteamento por provedor no envio de resposta"
```

---

## Task 9: Frontend — seletor de provedor, formulário Z-API, QR code

**Files:**
- Modify: `apps/web/src/components/WhatsAppConnectionPanel.tsx`
- Modify: `apps/web/__tests__/WhatsAppConnectionPanel.test.tsx`

**Interfaces:**
- Consumes: `POST whatsapp/connect-zapi`, `GET whatsapp/zapi-status`, `GET whatsapp/zapi-qrcode` (Tasks 3-4); `WhatsAppConnectionOut.provider` (Task 3).

- [ ] **Step 1: Ler o componente e o teste atuais por completo**

Ambos já foram lidos nesta sessão (`WhatsAppConnectionPanel.tsx` completo, início de `WhatsAppConnectionPanel.test.tsx`) — releia o arquivo de teste até o final antes de editar, pra não duplicar padrões de mock já existentes.

- [ ] **Step 1b: Atualizar o teste existente que quebra com a mudança de comportamento**

O teste já existente `"mostra o formulário quando não há conexão"` (linha ~18-24 do arquivo atual) espera que, sem conexão nenhuma, o formulário da Meta ("Phone Number ID") apareça direto. Isso muda de propósito nesta task: sem conexão, agora aparece primeiro o seletor de provedor, e só depois de escolher "WhatsApp Business oficial" é que o formulário da Meta aparece. Atualize esse teste:

```tsx
it("mostra o seletor de provedor quando não há conexão, e o formulário da Meta ao escolher", async () => {
  mockedBackendFetch.mockResolvedValue({ ok: true, json: async () => null });

  render(<WhatsAppConnectionPanel />);

  await waitFor(() =>
    expect(screen.getByRole("button", { name: /whatsapp business oficial/i })).toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole("button", { name: /whatsapp business oficial/i }));

  expect(screen.getByText("Phone Number ID")).toBeInTheDocument();
});
```

Adicionar `fireEvent` ao import de `@testing-library/react` no topo do arquivo, se ainda não estiver lá.

- [ ] **Step 2: Escrever os testes que falham**

Adicionar a `apps/web/__tests__/WhatsAppConnectionPanel.test.tsx`:

```tsx
it("mostra o seletor de provedor quando não há conexão", async () => {
  mockedBackendFetch.mockImplementation(async (path: string) => {
    if (path === "whatsapp/connection") return { ok: true, json: async () => null };
    if (path === "whatsapp/webhook-config") return { ok: false, json: async () => null };
    return { ok: false, json: async () => null };
  });

  render(<WhatsAppConnectionPanel />);

  await waitFor(() =>
    expect(screen.getByRole("button", { name: /z-api/i })).toBeInTheDocument(),
  );
  expect(screen.getByRole("button", { name: /whatsapp business oficial/i })).toBeInTheDocument();
});

it("mostra o formulário Z-API e conecta com sucesso, exibindo o QR code", async () => {
  mockedBackendFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "whatsapp/connection") return { ok: true, json: async () => null };
    if (path === "whatsapp/webhook-config") return { ok: false, json: async () => null };
    if (path === "whatsapp/connect-zapi" && init?.method === "POST") {
      return {
        ok: true,
        json: async () => ({
          provider: "zapi",
          display_phone_number: "Aguardando pareamento",
          status: "disconnected",
          connected_at: "2026-07-29T12:00:00Z",
        }),
      };
    }
    if (path === "whatsapp/zapi-qrcode") {
      return { ok: true, json: async () => ({ qrcode_base64: "data:image/png;base64,AAAA" }) };
    }
    return { ok: false, json: async () => null };
  });

  render(<WhatsAppConnectionPanel />);

  await waitFor(() => expect(screen.getByRole("button", { name: /z-api/i })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: /z-api/i }));

  fireEvent.change(screen.getByLabelText(/instance id/i), { target: { value: "inst-123" } });
  fireEvent.change(screen.getByLabelText(/^token$/i), { target: { value: "token-abc" } });
  fireEvent.click(screen.getByRole("button", { name: /conectar/i }));

  await waitFor(() => expect(screen.getByAltText(/qr code/i)).toBeInTheDocument());
});

it("mostra 'Conectado via Z-API' quando o provider é zapi e já está conectado", async () => {
  mockedBackendFetch.mockImplementation(async (path: string) => {
    if (path === "whatsapp/connection") {
      return {
        ok: true,
        json: async () => ({
          provider: "zapi",
          display_phone_number: "5511999998888",
          status: "connected",
          connected_at: "2026-07-29T12:00:00Z",
        }),
      };
    }
    if (path === "whatsapp/webhook-config") return { ok: false, json: async () => null };
    return { ok: false, json: async () => null };
  });

  render(<WhatsAppConnectionPanel />);

  await waitFor(() => expect(screen.getByText(/conectado via z-api/i)).toBeInTheDocument());
});
```

- [ ] **Step 3: Rodar os testes e confirmar que falham**

Run: `cd apps/web && pnpm vitest run __tests__/WhatsAppConnectionPanel.test.tsx -t "Z-API"`
Expected: falha — seletor/formulário/QR code ainda não existem.

- [ ] **Step 4: Implementar**

Substituir o conteúdo inteiro de `apps/web/src/components/WhatsAppConnectionPanel.tsx` por este (o formulário Meta e a lógica de `handleSubmit`/`handleDisconnect` originais ficam intocados em espírito — só ganham as ramificações de provedor descritas abaixo do bloco):

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { backendFetch } from "@/lib/client-api";

type Provider = "meta" | "zapi";

type Connection = {
  provider: Provider;
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

type ZApiFormState = { instance_id: string; instance_token: string; client_token: string };

const EMPTY_ZAPI_FORM: ZApiFormState = { instance_id: "", instance_token: "", client_token: "" };

type WebhookConfig = { callback_url: string; verify_token: string };

function extractErrorDetail(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

const STATUS_LABEL: Record<Connection["status"], string> = {
  connected: "conectado",
  disconnected: "desconectado",
};

const STATUS_CLASS: Record<Connection["status"], string> = {
  connected: "bg-accent-soft text-accent",
  disconnected: "bg-brass-soft text-brass",
};

const PROVIDER_LABEL: Record<Provider, string> = {
  meta: "WhatsApp Business oficial",
  zapi: "Z-API",
};

export function WhatsAppConnectionPanel() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [providerChoice, setProviderChoice] = useState<Provider | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [zapiForm, setZapiForm] = useState<ZApiFormState>(EMPTY_ZAPI_FORM);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [webhookConfig, setWebhookConfig] = useState<WebhookConfig | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [qrcode, setQrcode] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function load() {
    try {
      const response = await backendFetch("whatsapp/connection");
      if (response.ok) {
        setConnection(await response.json());
      }
      const configResponse = await backendFetch("whatsapp/webhook-config");
      if (configResponse.ok) {
        const config = await configResponse.json().catch(() => null);
        if (config?.callback_url && config?.verify_token) {
          setWebhookConfig(config);
        }
      }
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void load();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  function startZApiPolling() {
    stopPolling();
    pollRef.current = setInterval(async () => {
      const response = await backendFetch("whatsapp/zapi-status");
      if (!response.ok) return;
      const body = await response.json().catch(() => null);
      if (body?.status === "connected") {
        setConnection(body);
        setQrcode(null);
        setShowForm(false);
        setProviderChoice(null);
        stopPolling();
      }
    }, 3000);
  }

  function backToPicker() {
    setProviderChoice(null);
    setQrcode(null);
    stopPolling();
    setFeedback(null);
  }

  function startReconnect() {
    setShowForm(true);
    backToPicker();
  }

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
        setFeedback(extractErrorDetail(body, "Falha ao conectar — tente novamente."));
        return;
      }
      setConnection(body);
      setShowForm(false);
      setProviderChoice(null);
      setForm(EMPTY_FORM);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleZApiSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setSubmitting(true);
    try {
      const response = await backendFetch("whatsapp/connect-zapi", {
        method: "POST",
        body: JSON.stringify({
          instance_id: zapiForm.instance_id,
          instance_token: zapiForm.instance_token,
          client_token: zapiForm.client_token || null,
        }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(body, "Falha ao conectar — tente novamente."));
        return;
      }
      // Continua em showForm=true / providerChoice="zapi" — a próxima tela
      // (QR code) ainda faz parte do fluxo de conexão, não da conexão pronta.
      setConnection(body);
      setZapiForm(EMPTY_ZAPI_FORM);

      const qrResponse = await backendFetch("whatsapp/zapi-qrcode");
      if (qrResponse.ok) {
        const qrBody = await qrResponse.json().catch(() => null);
        if (qrBody?.qrcode_base64) {
          setQrcode(qrBody.qrcode_base64);
          startZApiPolling();
        }
      }
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
        setFeedback(extractErrorDetail(body, "Falha ao desconectar — tente novamente."));
        return;
      }
      setConnection(body);
      setQrcode(null);
      stopPolling();
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    }
  }

  async function handleCopy(field: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(field);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      // clipboard indisponível (http/permissão) — sem feedback, sem quebrar
    }
  }

  if (!loaded) {
    return (
      <main className="flex flex-1 items-center justify-center bg-ground text-sm text-muted">
        Carregando...
      </main>
    );
  }

  const inConnectFlow = !connection || showForm;
  const activeProvider = providerChoice ?? connection?.provider ?? null;
  const showMetaInstructions = webhookConfig && activeProvider !== "zapi";

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      <header className="border-b border-line px-8 py-5">
        <h1 className="font-display text-xl font-semibold text-ink">WhatsApp Business</h1>
        <p className="text-sm text-muted">
          Conecte o número de WhatsApp do escritório para os agentes atenderem pelo canal — pela
          via oficial da Meta ou pela Z-API.
        </p>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {!inConnectFlow && connection ? (
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
              Conectado via {PROVIDER_LABEL[connection.provider]} · Vinculado em{" "}
              {new Date(connection.connected_at).toLocaleDateString("pt-BR")}
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
                onClick={startReconnect}
                className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
              >
                {connection.status === "connected" ? "Trocar número" : "Reconectar"}
              </button>
            </div>
          </div>
        ) : providerChoice === null ? (
          <div className="flex max-w-md flex-col gap-3">
            <p className="text-sm text-ink">Como você quer conectar o WhatsApp?</p>
            <button
              type="button"
              onClick={() => setProviderChoice("meta")}
              className="rounded border border-line bg-surface px-4 py-3 text-left text-sm text-ink transition-colors hover:border-accent"
            >
              WhatsApp Business oficial
              <span className="mt-0.5 block text-xs text-muted">
                Via oficial da Meta — exige aprovação de negócio, mais burocrático.
              </span>
            </button>
            <button
              type="button"
              onClick={() => setProviderChoice("zapi")}
              className="rounded border border-line bg-surface px-4 py-3 text-left text-sm text-ink transition-colors hover:border-accent"
            >
              Z-API
              <span className="mt-0.5 block text-xs text-muted">
                Conexão por QR code, sem aprovação de negócio — mais simples de configurar.
              </span>
            </button>
            {connection && (
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="mt-1 w-fit font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
              >
                Cancelar
              </button>
            )}
          </div>
        ) : providerChoice === "meta" ? (
          <form onSubmit={handleSubmit} className="flex max-w-md flex-col gap-4">
            <button
              type="button"
              onClick={backToPicker}
              className="w-fit font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
            >
              ← Escolher outro provedor
            </button>
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
                  onClick={() => {
                    setShowForm(false);
                    setForm(EMPTY_FORM);
                  }}
                  className="font-mono text-xs uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                >
                  Cancelar
                </button>
              )}
            </div>
          </form>
        ) : qrcode ? (
          <div className="flex max-w-md flex-col gap-4">
            <p className="text-sm text-ink">
              Abra o WhatsApp do número que vai atender, vá em Aparelhos conectados e escaneie o
              QR code abaixo.
            </p>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={qrcode} alt="QR code de pareamento da Z-API" className="max-w-xs" />
            <p className="text-xs text-muted">Aguardando pareamento...</p>
            <button
              type="button"
              onClick={backToPicker}
              className="w-fit font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
            >
              Cancelar
            </button>
          </div>
        ) : (
          <form onSubmit={handleZApiSubmit} className="flex max-w-md flex-col gap-4">
            <button
              type="button"
              onClick={backToPicker}
              className="w-fit font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
            >
              ← Escolher outro provedor
            </button>
            <label className="flex flex-col gap-1 text-sm text-ink">
              Instance ID
              <input
                required
                value={zapiForm.instance_id}
                onChange={(event) =>
                  setZapiForm({ ...zapiForm, instance_id: event.target.value })
                }
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-ink">
              Token
              <input
                required
                type="password"
                value={zapiForm.instance_token}
                onChange={(event) =>
                  setZapiForm({ ...zapiForm, instance_token: event.target.value })
                }
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-ink">
              Client-Token (opcional)
              <input
                type="password"
                value={zapiForm.client_token}
                onChange={(event) =>
                  setZapiForm({ ...zapiForm, client_token: event.target.value })
                }
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
                  onClick={() => {
                    setShowForm(false);
                    setZapiForm(EMPTY_ZAPI_FORM);
                  }}
                  className="font-mono text-xs uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                >
                  Cancelar
                </button>
              )}
            </div>
          </form>
        )}

        {showMetaInstructions && (
          <section className="mt-8 max-w-xl rounded border border-line bg-surface p-6">
            <h2 className="font-display text-base font-semibold text-ink">
              Conectar o WhatsApp Business
            </h2>
            <p className="mt-1 text-sm text-muted">
              Essa conexão é feita direto com a Meta (a empresa dona do WhatsApp) — dá um pouco
              de trabalho, mas só precisa ser feita uma única vez.
            </p>
            <ol className="mt-4 flex list-decimal flex-col gap-3 pl-5 text-sm text-ink">
              <li>
                Consiga um número de telefone que ainda não esteja em uso no WhatsApp comum
                nem no WhatsApp Business App — pode ser um chip novo, comprado só pra isso, ou
                um número que o escritório já tenha disponível.
                <span className="mt-0.5 block text-xs text-muted">
                  É esse número que vai enviar e receber as mensagens dos seus clientes — ele
                  fica exclusivo pra isso, então recomendamos não ser o número pessoal de
                  ninguém.
                </span>
              </li>
              <li>
                Acesse{" "}
                <a
                  href="https://developers.facebook.com/apps/"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent underline"
                >
                  developers.facebook.com
                </a>{" "}
                e crie um app pro seu escritório.
                <span className="mt-0.5 block text-xs text-muted">
                  É gratuito e leva 1 minuto — só um cadastro técnico exigido pelo WhatsApp, não
                  afeta seu uso normal do Facebook.
                </span>
              </li>
              <li>
                Dentro do app, você vai criar uma{" "}
                <a
                  href="https://business.facebook.com/settings/system-users"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent underline"
                >
                  &quot;conta de sistema&quot;
                </a>
                .
                <span className="mt-0.5 block text-xs text-muted">
                  Pense nela como um crachá de acesso que representa seu escritório perante o
                  WhatsApp, separado da sua conta pessoal.
                </span>
              </li>
              <li>
                Gere uma chave de acesso pra essa conta — é como uma senha que a plataforma vai
                usar pra mandar e receber mensagens em nome do seu escritório. Marque as duas
                opções de permissão do WhatsApp que aparecerem.
                <span className="mt-0.5 block text-xs text-muted">
                  Não tem erro — são só essas duas opções mesmo, pode marcar as duas.
                </span>
              </li>
              <li>
                Cadastre o{" "}
                <a
                  href="https://business.facebook.com/wa/manage/phone-numbers/"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent underline"
                >
                  número de telefone
                </a>{" "}
                do escritório. A Meta vai pedir um código de 6 dígitos pra confirmar.
                <span className="mt-0.5 block text-xs text-muted">
                  Você inventa esse código na hora — só serve pra essa confirmação, não precisa
                  anotar.
                </span>
              </li>
              <li>
                No painel do seu app, abra{" "}
                <span className="font-medium">WhatsApp → Configuration → Webhook</span> e clique
                em Edit.
                <span className="mt-0.5 block text-xs text-muted">
                  É essa tela que recebe as mensagens dos seus clientes e repassa pra Advoxs.
                </span>
              </li>
              <li>
                Cole os dois valores abaixo exatamente como estão e clique em Verify and save:
                <div className="mt-2 flex flex-col gap-2">
                  <div className="flex items-center gap-2">
                    <input
                      readOnly
                      aria-label="Callback URL"
                      value={webhookConfig!.callback_url}
                      className="flex-1 rounded border border-line bg-ground px-3 py-2 font-mono text-xs text-ink"
                    />
                    <button
                      type="button"
                      aria-label="Copiar Callback URL"
                      onClick={() => void handleCopy("url", webhookConfig!.callback_url)}
                      className="rounded border border-line px-3 py-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                    >
                      {copied === "url" ? "Copiado!" : "Copiar"}
                    </button>
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      readOnly
                      aria-label="Verify token"
                      value={webhookConfig!.verify_token}
                      className="flex-1 rounded border border-line bg-ground px-3 py-2 font-mono text-xs text-ink"
                    />
                    <button
                      type="button"
                      aria-label="Copiar Verify token"
                      onClick={() => void handleCopy("token", webhookConfig!.verify_token)}
                      className="rounded border border-line px-3 py-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                    >
                      {copied === "token" ? "Copiado!" : "Copiar"}
                    </button>
                  </div>
                </div>
              </li>
              <li>
                Ainda em Webhook, na lista{" "}
                <span className="font-medium">Webhook fields</span>, clique em Manage e assine o
                campo <code className="rounded bg-ground px-1">messages</code>.
                <span className="mt-0.5 block text-xs text-muted">
                  Sem assinar esse campo específico, o webhook fica configurado mas nunca é
                  acionado.
                </span>
              </li>
            </ol>
          </section>
        )}
      </div>
    </main>
  );
}
```

Nota: `webhookConfig!` (non-null assertion) é seguro aqui porque a seção inteira só renderiza dentro de `{showMetaInstructions && (...)}`, e `showMetaInstructions` já checa `webhookConfig` truthy — TypeScript não estreita esse tipo automaticamente através da variável derivada `showMetaInstructions`, daí a asserção explícita (mesma limitação existiria com qualquer variável booleana derivada; alternativa mais verbosa seria repetir `webhookConfig &&` em vez de usar `showMetaInstructions`, mas isso reintroduziria a condição de provedor em cada ponto — a asserção é o trade-off mais legível aqui).

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `cd apps/web && pnpm vitest run __tests__/WhatsAppConnectionPanel.test.tsx`
Expected: todos passam, incluindo os já existentes (não regredir o fluxo Meta).

- [ ] **Step 6: Rodar a suíte completa e lint**

Run: `cd apps/web && pnpm test && pnpm lint`

- [ ] **Step 7: Commit**

```bash
cd apps/web
git add src/components/WhatsAppConnectionPanel.tsx __tests__/WhatsAppConnectionPanel.test.tsx
git commit -m "feat(web): seletor de provedor, formulário Z-API e exibição de QR code"
```

---

## Task 10: Documentação (`CLAUDE.md`)

**Files:**
- Modify: `/home/falcao/development/advoxs/CLAUDE.md`

- [ ] **Step 1: Atualizar a seção "Integração WhatsApp Business"**

Ler o código final das Tasks 1-9 (não confiar de memória neste plano — pelo menos 2 detalhes podem ter mudado durante a execução: o endpoint exato de envio de documento da Z-API, confirmado só na Task 8/Step 3, e a estrutura final do `WhatsAppConnectionPanel.tsx`). Adicionar um parágrafo novo descrevendo: a coluna `provider` em `whatsapp_numbers`, os 3 endpoints novos (`connect-zapi`/`zapi-status`/`zapi-qrcode`), o webhook `/webhooks/zapi/{webhook_secret}` (e por que o segredo fica no path — Z-API não assina payload), o `ZApiClient` no `agents`, e a limitação da Task 6 (cobrança do cliente final bloqueada pra Z-API). Referenciar a spec: `docs/superpowers/specs/2026-07-29-whatsapp-zapi-design.md`. Seguir o estilo já estabelecido no arquivo (`✅` no início de blocos prontos, travessões citando arquivo/função exatos, "porquê" de decisões deliberadas).

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: documenta a conexao de WhatsApp via Z-API"
```
