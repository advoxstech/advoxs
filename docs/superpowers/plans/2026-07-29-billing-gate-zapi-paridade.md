# Billing Gate via Z-API — Paridade de Provedor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o billing gate determinístico (cobrança do cliente final) funcionar de forma equivalente pra tenants conectados via Z-API, removendo as duas guardas que hoje bloqueiam essa combinação.

**Architecture:** Um cliente HTTP Z-API novo no `worker` (duplicado deliberadamente, mesmo padrão já usado pro cliente Meta do worker) ganha uma função de lista interativa (`send_zapi_option_list`, endpoint `send-option-list` da Z-API). `billing_gate.py` passa a rotear o envio de texto/lista por provedor através de dois helpers internos (`_send_text`/`_send_list`), em vez de assumir Meta e passar um `access_token` já descriptografado por 4 funções. O parser de webhook da Z-API aprende a reconhecer a resposta de uma lista (`listResponseMessage.title`), que já chega no mesmo formato de texto que a resolução de pacote por nome já espera — nenhuma mudança na lógica de resolução de pacote. As duas guardas de bloqueio (rota de API + `maybe_enter_gate` no worker) são removidas.

**Tech Stack:** Python 3.12/3.13, FastAPI, SQLAlchemy async, httpx, pytest + pytest-asyncio.

## Global Constraints

- Endpoint da Z-API confirmado: `POST https://api.z-api.io/instances/{instance_id}/token/{token}/send-option-list`, payload `{"phone": str, "message": str, "optionList": {"title": str, "buttonLabel": str, "options": [{"id": str, "title": str, "description": str}]}}`.
- Webhook de resposta da Z-API: quando o usuário escolhe uma opção, o payload traz `"listResponseMessage": {"title": str, "selectedRowId": str}` em vez de `"text"`.
- `apps/worker/app/crypto.py::decrypt_access_token(value: str) -> str` levanta `AttributeError` se `value` for `None` — todo campo `zapi_*` opcional (`zapi_client_token_encrypted`) só pode ser descriptografado quando não for `None`.
- Nenhuma mudança de schema em `InboundContext` (`apps/worker/app/tasks/inbound_context.py`) — todos os campos necessários (`whatsapp_provider`, `zapi_instance_id`, `zapi_instance_token_encrypted`, `zapi_client_token_encrypted`) já existem.
- `apps/agents` não é tocado por este plano — o `ZApiClient` dele já cobre o fluxo normal de resposta do agente; esta feature cobre só o billing gate, que manda mensagem direto do `worker`.
- Seguir o padrão de exceções já estabelecido: `ZApiNetworkError` (falha de rede) e `ZApiApiError` (erro retornado pela própria Z-API), mesmos nomes usados em `apps/api/app/clients/zapi.py`.
- Rodar testes do `apps/worker` e `apps/api` com `python3 -m pytest` (não `uv run` — venv quebrado nesta máquina para `apps/agents`; `apps/worker`/`apps/api` usam `uv run pytest` normalmente, mas confirme com `uv run pytest --version` antes — se falhar, caia pra `python3 -m pytest`).

---

### Task 1: Cliente Z-API do worker (texto + lista interativa)

**Files:**
- Create: `apps/worker/app/clients/zapi.py`
- Test: `apps/worker/tests/unit/test_zapi_client.py`

**Interfaces:**
- Consumes: nada de tasks anteriores (primeira task).
- Produces:
  - `class ZApiNetworkError(Exception)`
  - `class ZApiApiError(Exception)`
  - `async def send_zapi_text_message(instance_id: str, token: str, client_token: str | None, to: str, text: str) -> None`
  - `async def send_zapi_option_list(instance_id: str, token: str, client_token: str | None, to: str, message: str, title: str, button_label: str, options: list[dict]) -> None` — `options`: `[{"id": str, "title": str, "description": str}]`.

Essas 4 entidades são consumidas pela Task 3 (`billing_gate.py`).

- [ ] **Step 1: Escrever o arquivo do cliente**

```python
"""Cliente HTTP da Z-API usado direto pelo worker — só pelo billing gate
determinístico (apps/worker/app/billing_gate.py), que precisa mandar texto
e listas interativas SEM passar pelo agents service (é esse desvio que
elimina o custo de LLM nesse trecho do funil). Duplicado deliberadamente de
apps/api/app/clients/zapi.py — mesmo padrão já usado no projeto pra evitar
acoplamento entre serviços deployados separadamente (ver
apps/worker/app/clients/whatsapp.py, que já duplica o cliente Meta do api
pelo mesmo motivo)."""

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


def _zapi_error_message(response: httpx.Response, fallback: str) -> str:
    try:
        body = response.json()
    except ValueError:
        return fallback
    if isinstance(body, dict):
        for key in ("error", "message", "msg"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return fallback


async def send_zapi_text_message(
    instance_id: str, token: str, client_token: str | None, to: str, text: str
) -> None:
    """Envia mensagem de texto pela Z-API — equivalente a
    app.clients.whatsapp.send_text_message (Meta)."""
    url = _instance_url(instance_id, token, "send-text")
    payload = {"phone": to, "message": text}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, headers=_headers(client_token), json=payload)
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao enviar mensagem pela Z-API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Z-API (send-text) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError(
            _zapi_error_message(response, "Não foi possível enviar a mensagem pela Z-API")
        )


async def send_zapi_option_list(
    instance_id: str,
    token: str,
    client_token: str | None,
    to: str,
    message: str,
    title: str,
    button_label: str,
    options: list[dict],
) -> None:
    """Envia uma lista de opções pela Z-API (`send-option-list`) — equivalente
    a app.clients.whatsapp.send_interactive_list_message (Meta), mas sem o
    conceito de seções nomeadas: `options` é uma lista flat, cada item
    `{"id": str, "title": str, "description": str}` (mesmo formato de linha
    já usado pra Meta)."""
    url = _instance_url(instance_id, token, "send-option-list")
    payload = {
        "phone": to,
        "message": message,
        "optionList": {"title": title, "buttonLabel": button_label, "options": options},
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, headers=_headers(client_token), json=payload)
    except httpx.HTTPError as exc:
        raise ZApiNetworkError(f"Falha de rede ao enviar lista pela Z-API: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Z-API (send-option-list) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ZApiApiError(
            _zapi_error_message(response, "Não foi possível enviar a lista pela Z-API")
        )
```

- [ ] **Step 2: Escrever os testes (já falhando, arquivo novo)**

```python
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import app.clients.zapi as zapi_client
from app.clients.zapi import ZApiApiError, ZApiNetworkError, send_zapi_option_list, send_zapi_text_message


def _mock_async_client(monkeypatch, response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.post.return_value = response
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(zapi_client.httpx, "AsyncClient", MagicMock(return_value=cm))
    return client


def _response(status_code: int, json_body: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.is_error = status_code >= 400
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


class TestSendZApiTextMessage:
    async def test_envia_texto_com_sucesso(self, monkeypatch) -> None:
        response = _response(200, {"zaapId": "z1", "messageId": "m1", "id": "i1"})
        client = _mock_async_client(monkeypatch, response)

        await send_zapi_text_message(
            instance_id="inst-1", token="token-1", client_token=None, to="5511999998888", text="Olá"
        )

        client.post.assert_awaited_once()
        args, kwargs = client.post.call_args
        assert args[0] == "https://api.z-api.io/instances/inst-1/token/token-1/send-text"
        assert kwargs["json"] == {"phone": "5511999998888", "message": "Olá"}
        assert "Client-Token" not in kwargs["headers"]

    async def test_inclui_client_token_no_header_quando_presente(self, monkeypatch) -> None:
        response = _response(200, {})
        client = _mock_async_client(monkeypatch, response)

        await send_zapi_text_message(
            instance_id="inst-1",
            token="token-1",
            client_token="client-tok",
            to="5511999998888",
            text="Olá",
        )

        assert client.post.call_args.kwargs["headers"]["Client-Token"] == "client-tok"

    async def test_erro_de_rede_levanta_zapi_network_error(self, monkeypatch) -> None:
        client = AsyncMock()
        client.post.side_effect = httpx.ConnectError("down")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(zapi_client.httpx, "AsyncClient", MagicMock(return_value=cm))

        with pytest.raises(ZApiNetworkError):
            await send_zapi_text_message(
                instance_id="inst-1", token="token-1", client_token=None, to="5511999998888", text="Olá"
            )

    async def test_erro_http_levanta_zapi_api_error(self, monkeypatch) -> None:
        response = _response(400, {"error": "instância não encontrada"})
        _mock_async_client(monkeypatch, response)

        with pytest.raises(ZApiApiError, match="instância não encontrada"):
            await send_zapi_text_message(
                instance_id="inst-1", token="token-1", client_token=None, to="5511999998888", text="Olá"
            )


class TestSendZApiOptionList:
    async def test_envia_lista_com_sucesso(self, monkeypatch) -> None:
        response = _response(200, {"zaapId": "z1", "messageId": "m1", "id": "i1"})
        client = _mock_async_client(monkeypatch, response)
        options = [{"id": "Básico", "title": "Básico", "description": "R$ 49.90 = 500 créditos"}]

        await send_zapi_option_list(
            instance_id="inst-1",
            token="token-1",
            client_token=None,
            to="5511999998888",
            message="Escolha uma opção:",
            title="Pacotes de créditos",
            button_label="Ver opções",
            options=options,
        )

        client.post.assert_awaited_once()
        args, kwargs = client.post.call_args
        assert args[0] == "https://api.z-api.io/instances/inst-1/token/token-1/send-option-list"
        assert kwargs["json"] == {
            "phone": "5511999998888",
            "message": "Escolha uma opção:",
            "optionList": {
                "title": "Pacotes de créditos",
                "buttonLabel": "Ver opções",
                "options": options,
            },
        }

    async def test_erro_http_levanta_zapi_api_error(self, monkeypatch) -> None:
        response = _response(500, {})
        _mock_async_client(monkeypatch, response)

        with pytest.raises(ZApiApiError):
            await send_zapi_option_list(
                instance_id="inst-1",
                token="token-1",
                client_token=None,
                to="5511999998888",
                message="m",
                title="t",
                button_label="b",
                options=[],
            )

    async def test_erro_de_rede_levanta_zapi_network_error(self, monkeypatch) -> None:
        client = AsyncMock()
        client.post.side_effect = httpx.ConnectTimeout("timeout")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(zapi_client.httpx, "AsyncClient", MagicMock(return_value=cm))

        with pytest.raises(ZApiNetworkError):
            await send_zapi_option_list(
                instance_id="inst-1",
                token="token-1",
                client_token=None,
                to="5511999998888",
                message="m",
                title="t",
                button_label="b",
                options=[],
            )
```

- [ ] **Step 3: Rodar os testes e confirmar que passam**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_zapi_client.py -v`
Expected: 6 testes PASS (o arquivo de implementação já existe do Step 1, então não há um "fail" intermediário aqui — mas rode mesmo assim pra confirmar).

- [ ] **Step 4: Commit**

```bash
git add apps/worker/app/clients/zapi.py apps/worker/tests/unit/test_zapi_client.py
git commit -m "feat(worker): cliente Z-API pro billing gate (texto e lista interativa)"
```

---

### Task 2: Parsing de resposta de lista no webhook Z-API

**Files:**
- Modify: `apps/api/app/schemas/whatsapp.py` (função `extract_inbound_zapi_message`, linhas 95-117 hoje)
- Test: `apps/api/tests/unit/test_whatsapp_schemas.py`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces: `extract_inbound_zapi_message(payload: dict) -> InboundZApiMessage | None` — schema (`InboundZApiMessage`) inalterado, só o `content` passa a poder vir de `listResponseMessage.title` além de `text.message`. Nenhuma task futura deste plano depende diretamente desta função (a Task 3 mexe só no `worker`), mas é o mecanismo que faz a seleção do usuário chegar como texto normal em `messages.content` — sem isso, `_resolve_package_by_title` (`billing_gate.py`) nunca veria a escolha do cliente final via Z-API.

- [ ] **Step 1: Escrever os testes falhando**

Adicionar ao final de `apps/api/tests/unit/test_whatsapp_schemas.py` (mesmo arquivo, reaproveita o helper `_zapi_payload` já existente nas linhas 114-123):

```python
def test_extract_zapi_resposta_de_lista_usa_o_title() -> None:
    payload = _zapi_payload()
    del payload["text"]
    payload["listResponseMessage"] = {"title": "Básico", "selectedRowId": "Básico"}

    result = extract_inbound_zapi_message(payload)

    assert result is not None
    assert result.content == "Básico"


def test_extract_zapi_lista_tem_prioridade_sobre_texto() -> None:
    payload = _zapi_payload()
    payload["listResponseMessage"] = {"title": "Premium", "selectedRowId": "Premium"}

    result = extract_inbound_zapi_message(payload)

    assert result is not None
    assert result.content == "Premium"


def test_extract_zapi_lista_sem_title_ignora_e_cai_no_texto() -> None:
    payload = _zapi_payload()
    payload["listResponseMessage"] = {"selectedRowId": "Básico"}

    result = extract_inbound_zapi_message(payload)

    assert result is not None
    assert result.content == "Olá, preciso de ajuda"
```

- [ ] **Step 2: Rodar e confirmar que falham**

Run: `cd apps/api && python3 -m pytest tests/unit/test_whatsapp_schemas.py -k zapi_resposta_de_lista -v`
Expected: FAIL — `result.content` é `""` ou `None`, já que `listResponseMessage` ainda não é reconhecido.

- [ ] **Step 3: Implementar**

Substituir a função `extract_inbound_zapi_message` inteira em `apps/api/app/schemas/whatsapp.py` (linhas 95-117) por:

```python
def extract_inbound_zapi_message(payload: dict) -> InboundZApiMessage | None:
    """Extrai a mensagem de um payload de webhook da Z-API — diferente da
    Meta, cada POST já é 1 mensagem só, sem lote. Ignora eco de mensagem
    enviada pelo próprio WhatsApp Web conectado (fromMe=true) e mensagens
    sem texto (mídia recebida via Z-API não é processada nesta v1).

    Reconhece dois formatos de conteúdo: mensagem de texto simples
    (`text.message`) e resposta de uma lista interativa enviada pelo billing
    gate determinístico (`listResponseMessage.title`, quando o cliente final
    escolhe um pacote de créditos — ver apps/worker/app/billing_gate.py).
    `listResponseMessage` é checado primeiro porque uma resposta de lista não
    vem acompanhada de um campo `text` populado."""
    if payload.get("fromMe"):
        return None

    instance_id = payload.get("instanceId")
    message_id = payload.get("messageId")
    sender = payload.get("phone")

    list_response = payload.get("listResponseMessage")
    if isinstance(list_response, dict) and list_response.get("title"):
        content = list_response["title"]
    else:
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

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd apps/api && python3 -m pytest tests/unit/test_whatsapp_schemas.py -v`
Expected: todos PASS, incluindo os 3 novos e os 4 já existentes (`test_extract_zapi_*`).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/schemas/whatsapp.py apps/api/tests/unit/test_whatsapp_schemas.py
git commit -m "feat(api): reconhece resposta de lista interativa no webhook Z-API"
```

---

### Task 3: Roteamento por provedor no billing gate + lista achatada pra Z-API

**Files:**
- Modify: `apps/worker/app/billing_gate.py` (arquivo inteiro, 255 linhas — ver diff completo abaixo)
- Modify: `apps/worker/tests/unit/test_billing_gate.py`

**Interfaces:**
- Consumes (da Task 1): `send_zapi_text_message`, `send_zapi_option_list`, `ZApiNetworkError`, `ZApiApiError` de `app.clients.zapi`.
- Produces: `maybe_enter_gate` sem mais bloquear Z-API (mesma assinatura pública: `async def maybe_enter_gate(session, tenant_id, conversation_id, inbound) -> bool`). Nada depende de funções internas novas (`_send_text`, `_send_list`, `_split_by_kind`, `_packages_to_flat_options`) fora deste arquivo.

**Estado atual de `apps/worker/app/billing_gate.py` (pra referência do diff):** o arquivo importa `send_interactive_list_message`/`send_text_message` de `app.clients.whatsapp`, decripta `access_token` uma vez em `handle_billing_gate` e passa esse valor como parâmetro por `_open_gate`, `_send_package_list`, `_handle_package_selection` e `_handle_awaiting_payment`. `maybe_enter_gate` tem um early-return `if inbound.whatsapp_provider != "meta": return False` (linhas 40-41, com docstring nas linhas 29-39 explicando o bloqueio).

- [ ] **Step 1: Escrever/ajustar os testes primeiro**

Em `apps/worker/tests/unit/test_billing_gate.py`:

**1a. Inverter os 2 testes que hoje afirmam o bloqueio.** Substituir o corpo de `test_nao_entra_no_gate_para_tenant_zapi_mesmo_sem_saldo` (linhas 74-89) por:

```python
    async def test_entra_no_gate_para_tenant_zapi_sem_saldo(self) -> None:
        """Paridade de provedor: Z-API entra no gate exatamente como Meta —
        o gate manda mensagem de lista via send-option-list da Z-API em vez
        da Cloud API da Meta (ver TestHandleBillingGateAbertura mais abaixo
        pro envio de fato)."""
        session = AsyncMock()
        inbound = _inbound(
            whatsapp_provider="zapi", conversation_state="agent", end_customer_balance=Decimal(0)
        )

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is True
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()
```

E substituir o corpo de `test_ja_em_billing_gate_mas_tenant_migrou_pra_zapi_nao_reprocessa` (linhas 91-108) por:

```python
    async def test_ja_em_billing_gate_com_provider_zapi_retorna_true_sem_reprocessar(self) -> None:
        """Espelha test_ja_em_billing_gate_retorna_true_sem_reprocessar_entrada
        pro provider Z-API — o curto-circuito de reentrada não depende do
        provedor."""
        session = AsyncMock()
        inbound = _inbound(
            whatsapp_provider="zapi",
            conversation_state="billing_gate",
            billing_gate_step="aguardando_pagamento",
        )

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is True
        session.execute.assert_not_called()
```

**1b. Adicionar uma classe de testes nova pro roteamento de envio por provedor**, no final do arquivo:

```python
class TestEnvioPorProvedorZApi:
    async def test_abertura_do_gate_usa_zapi_quando_provider_e_zapi(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        send_list = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_zapi_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.send_zapi_option_list", send_list)
        inbound = _inbound(
            whatsapp_provider="zapi",
            zapi_instance_id="inst-1",
            zapi_instance_token_encrypted="cifrado-token",
            zapi_client_token_encrypted=None,
            billing_gate_step=None,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        send_text.assert_awaited_once()
        assert send_text.await_args.kwargs["instance_id"] == "inst-1"
        assert send_text.await_args.kwargs["client_token"] is None
        send_list.assert_awaited_once()
        options = send_list.await_args.kwargs["options"]
        assert options[0]["title"] == "Básico"
        assert options[1]["title"] == "Premium"

    async def test_lista_zapi_achata_avulso_e_assinatura(self, monkeypatch) -> None:
        session = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_zapi_text_message", AsyncMock())
        send_list = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_zapi_option_list", send_list)
        packages = [
            {
                "id": "p1", "name": "Básico", "price_brl": "49.90",
                "kind": "one_time", "credits_granted": 500,
            },
            {
                "id": "p2", "name": "Ilimitado", "price_brl": "99.90",
                "kind": "subscription", "credits_granted": None,
            },
        ]
        inbound = _inbound(
            whatsapp_provider="zapi",
            zapi_instance_id="inst-1",
            zapi_instance_token_encrypted="cifrado-token",
            end_customer_packages=packages,
            billing_gate_step=None,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        options = send_list.await_args.kwargs["options"]
        assert len(options) == 2
        assert options[0]["title"] == "Básico"
        assert options[1]["title"] == "Ilimitado"

    async def test_selecao_valida_via_zapi_gera_link(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        checkout = AsyncMock(return_value="https://checkout.stripe.com/xyz")
        monkeypatch.setattr("app.billing_gate.send_zapi_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.create_end_customer_checkout", checkout)
        inbound = _inbound(
            whatsapp_provider="zapi",
            zapi_instance_id="inst-1",
            zapi_instance_token_encrypted="cifrado-token",
            billing_gate_step="aguardando_selecao_pacote",
            message_content="Básico",
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        checkout.assert_awaited_once_with(
            tenant_id=TENANT_ID, contact_phone_number="5511999998888", package_id="pkg-1"
        )
        assert "https://checkout.stripe.com/xyz" in send_text.await_args.kwargs["text"]

    async def test_client_token_zapi_e_descriptografado_quando_presente(self, monkeypatch) -> None:
        session = AsyncMock()
        send_text = AsyncMock()
        monkeypatch.setattr("app.billing_gate.send_zapi_text_message", send_text)
        monkeypatch.setattr("app.billing_gate.send_zapi_option_list", AsyncMock())
        inbound = _inbound(
            whatsapp_provider="zapi",
            zapi_instance_id="inst-1",
            zapi_instance_token_encrypted="cifrado-token",
            zapi_client_token_encrypted="cifrado-client-token",
            billing_gate_step=None,
        )

        await handle_billing_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert send_text.await_args.kwargs["client_token"] == "token-claro"


class TestPackagesToFlatOptions:
    def test_achata_avulso_e_assinatura_avulso_primeiro(self) -> None:
        from app.billing_gate import _packages_to_flat_options

        packages = [
            {
                "id": "p2", "name": "Ilimitado", "price_brl": "99.90",
                "kind": "subscription", "credits_granted": None,
            },
            {
                "id": "p1", "name": "Básico", "price_brl": "49.90",
                "kind": "one_time", "credits_granted": 500,
            },
        ]

        options = _packages_to_flat_options(packages)

        assert [o["title"] for o in options] == ["Básico", "Ilimitado"]
        assert options[0]["description"] == "R$ 49.90 = 500 créditos"
        assert options[1]["description"] == "R$ 99.90/mês — conversas ilimitadas"

    def test_so_avulso(self) -> None:
        from app.billing_gate import _packages_to_flat_options

        packages = [
            {
                "id": "p1", "name": "Básico", "price_brl": "49.90",
                "kind": "one_time", "credits_granted": 500,
            },
        ]

        options = _packages_to_flat_options(packages)

        assert len(options) == 1
        assert options[0]["title"] == "Básico"
```

O fixture `crypto` (linha 50-52 do arquivo) já faz `monkeypatch.setattr("app.billing_gate.decrypt_access_token", lambda v: "token-claro")` — vale pra qualquer campo cifrado, incluindo os `zapi_*`, sem mudança.

- [ ] **Step 2: Rodar e confirmar que os testes novos falham**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_billing_gate.py -v`
Expected: FAIL nos testes novos (`ImportError`/`AttributeError` em `_packages_to_flat_options`, `send_zapi_text_message`/`send_zapi_option_list` inexistentes no módulo) e nos 2 testes invertidos (ainda esperam bloqueio, então falham contra a implementação atual até o Step 3).

- [ ] **Step 3: Reescrever `apps/worker/app/billing_gate.py` inteiro**

```python
"""Máquina de estados do billing gate determinístico — conduz o diálogo
mecânico (sem LLM) de "sem saldo -> escolher pacote -> pagar -> liberado"
pro cliente final, sempre que tenant_billing_settings.enabled = true — é o
único mecanismo de cobrança do cliente final que existe (ver
docs/superpowers/specs/2026-07-23-gate-unico-deterministico-design.md).
Funciona igual nos dois provedores de WhatsApp (Meta e Z-API) — ver
docs/superpowers/specs/2026-07-29-billing-gate-zapi-paridade-design.md."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import tables
from app.clients.billing import create_end_customer_checkout
from app.clients.whatsapp import send_interactive_list_message, send_text_message
from app.clients.zapi import send_zapi_option_list, send_zapi_text_message
from app.crypto import decrypt_access_token
from app.tasks.inbound_context import InboundContext

MAX_RETRIES = 3


async def maybe_enter_gate(
    session: AsyncSession, tenant_id: str, conversation_id: str, inbound: InboundContext
) -> bool:
    """Transiciona a conversa pra billing_gate se o tenant estiver migrado e
    o contato sem saldo. Retorna True se a conversa está (ou acabou de
    entrar) em billing_gate — nesse caso, process_inbound_message não deve
    seguir pro fluxo normal de chamar o agents."""
    if inbound.conversation_state == "billing_gate":
        if inbound.end_customer_billing_exempt or inbound.end_customer_has_active_subscription:
            await session.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == uuid.UUID(conversation_id))
                .values(state="agent", billing_gate_step=None, billing_gate_retries=0)
            )
            await session.commit()
            return False
        return True
    if (
        inbound.conversation_state == "agent"
        and inbound.end_customer_billing_enabled
        and not inbound.end_customer_billing_exempt
        and not inbound.end_customer_has_active_subscription
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


def _zapi_client_token(inbound: InboundContext) -> str | None:
    if not inbound.zapi_client_token_encrypted:
        return None
    return decrypt_access_token(inbound.zapi_client_token_encrypted)


async def _send_text(inbound: InboundContext, text: str) -> None:
    if inbound.whatsapp_provider == "zapi":
        await send_zapi_text_message(
            instance_id=inbound.zapi_instance_id,
            token=decrypt_access_token(inbound.zapi_instance_token_encrypted),
            client_token=_zapi_client_token(inbound),
            to=inbound.contact_phone_number,
            text=text,
        )
        return
    await send_text_message(
        phone_number_id=inbound.phone_number_id,
        access_token=decrypt_access_token(inbound.access_token_encrypted),
        to=inbound.contact_phone_number,
        text=text,
    )


async def _send_list(inbound: InboundContext) -> None:
    if inbound.whatsapp_provider == "zapi":
        await send_zapi_option_list(
            instance_id=inbound.zapi_instance_id,
            token=decrypt_access_token(inbound.zapi_instance_token_encrypted),
            client_token=_zapi_client_token(inbound),
            to=inbound.contact_phone_number,
            message="Escolha uma opção:",
            title="Pacotes de créditos",
            button_label="Ver opções",
            options=_packages_to_flat_options(inbound.end_customer_packages),
        )
        return
    await send_interactive_list_message(
        phone_number_id=inbound.phone_number_id,
        access_token=decrypt_access_token(inbound.access_token_encrypted),
        to=inbound.contact_phone_number,
        header="Pacotes de créditos",
        body="Escolha uma opção:",
        sections=_packages_to_sections(inbound.end_customer_packages),
    )


async def handle_billing_gate(
    session: AsyncSession, tenant_id: str, conversation_id: str, inbound: InboundContext
) -> None:
    if inbound.billing_gate_step is None:
        await _open_gate(session, tenant_id, conversation_id, inbound)
    elif inbound.billing_gate_step == "aguardando_selecao_pacote":
        await _handle_package_selection(session, tenant_id, conversation_id, inbound)
    elif inbound.billing_gate_step == "aguardando_pagamento":
        await _handle_awaiting_payment(session, conversation_id, inbound)


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


def _package_row(package: dict) -> dict:
    if package.get("kind") == "subscription":
        description = f"R$ {package['price_brl']}/mês — conversas ilimitadas"
    else:
        description = f"R$ {package['price_brl']} = {package['credits_granted']} créditos"
    return {"id": package["name"], "title": package["name"], "description": description}


def _split_by_kind(packages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separa pacotes avulsos de assinaturas, preservando a ordem original
    dentro de cada grupo — avulsos sempre aparecem primeiro na UI, tanto na
    lista em seções da Meta quanto na lista achatada da Z-API."""
    avulsos = [p for p in packages if p.get("kind", "one_time") != "subscription"]
    assinaturas = [p for p in packages if p.get("kind") == "subscription"]
    return avulsos, assinaturas


def _packages_to_sections(packages: list[dict]) -> list[dict]:
    avulsos, assinaturas = _split_by_kind(packages)

    if not assinaturas:
        return [{"title": "Pacotes disponíveis", "rows": [_package_row(p) for p in avulsos]}]

    sections = []
    if avulsos:
        sections.append(
            {"title": "Pacotes de créditos", "rows": [_package_row(p) for p in avulsos]}
        )
    sections.append(
        {"title": "Assinatura mensal", "rows": [_package_row(p) for p in assinaturas]}
    )
    return sections


def _packages_to_flat_options(packages: list[dict]) -> list[dict]:
    """Z-API (`send-option-list`) não tem o conceito de seções nomeadas do
    formato de lista da Meta — junta tudo numa lista só, avulsos primeiro; a
    description de cada pacote (R$ X = Y créditos vs R$ X/mês — ilimitado)
    já distingue avulso de assinatura sem precisar de cabeçalho de seção."""
    avulsos, assinaturas = _split_by_kind(packages)
    return [_package_row(p) for p in avulsos + assinaturas]


async def _open_gate(
    session: AsyncSession, tenant_id: str, conversation_id: str, inbound: InboundContext
) -> None:
    text = await _welcome_text(
        session, tenant_id, inbound.contact_phone_number, inbound.billing_gate_welcome_text
    )
    await _send_text(inbound, text)
    await _send_list(inbound)
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
    session: AsyncSession, tenant_id: str, conversation_id: str, inbound: InboundContext
) -> None:
    package = _resolve_package_by_title(inbound.end_customer_packages, inbound.message_content)
    if package is None:
        retries = inbound.billing_gate_retries + 1
        if retries >= MAX_RETRIES:
            await _escalate_to_human(session, conversation_id)
            return
        await _send_text(inbound, "Não entendi — escolha uma opção da lista abaixo:")
        await _send_list(inbound)
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
    await _send_text(inbound, f"Aqui está o link de pagamento: {checkout_url}")
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
    session: AsyncSession, conversation_id: str, inbound: InboundContext
) -> None:
    retries = inbound.billing_gate_retries + 1
    if retries >= MAX_RETRIES:
        await _escalate_to_human(session, conversation_id)
        return
    await _send_text(
        inbound,
        (
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

Notas sobre este diff:
- `maybe_enter_gate` perdeu o parágrafo inicial do docstring e o `if inbound.whatsapp_provider != "meta": return False` — não bloqueia mais nenhum provedor.
- `handle_billing_gate`, `_open_gate`, `_handle_package_selection` e `_handle_awaiting_payment` perderam o parâmetro `access_token: str` — quem decide a credencial certa agora é `_send_text`/`_send_list`, chamados só com `inbound`.
- `_send_package_list` (a função antiga que só existia pra montar+mandar a lista da Meta) foi removida — `_send_list` a substitui e já cobre os dois provedores.

- [ ] **Step 4: Rodar os testes e confirmar que todos passam**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_billing_gate.py -v`
Expected: todos PASS — os 2 testes invertidos, os novos de roteamento Z-API, os de `_packages_to_flat_options`, e TODOS os testes pré-existentes que exercitam o caminho Meta (`TestHandleBillingGateAbertura`, `TestHandleBillingGateSelecaoPacote`, `TestHandleBillingGateAguardandoPagamento`, `TestPackagesToSections`) sem nenhuma mudança neles — eles continuam patcheando `app.billing_gate.send_text_message`/`app.billing_gate.send_interactive_list_message` diretamente, e como `_send_text`/`_send_list` chamam esses mesmos nomes de módulo pro caminho Meta, o patch continua funcionando.

- [ ] **Step 5: Rodar a suíte inteira do worker (garantir zero regressão fora deste arquivo)**

Run: `cd apps/worker && python3 -m pytest tests/unit -v`
Expected: todos PASS (inclui `test_load_context.py`, que não muda nesta task).

- [ ] **Step 6: Commit**

```bash
git add apps/worker/app/billing_gate.py apps/worker/tests/unit/test_billing_gate.py
git commit -m "feat(worker): billing gate roteia envio por provedor (Meta e Z-API)"
```

---

### Task 4: Remover o bloqueio de habilitar cobrança pra tenant Z-API

**Files:**
- Modify: `apps/api/app/api/v1/end_customer_billing.py` (linhas 19, 155-166 hoje)
- Modify: `apps/api/tests/unit/test_end_customer_billing_settings_routes.py`

**Interfaces:**
- Consumes: nada de tasks anteriores (independente das Tasks 1-3, mexe só em `apps/api`).
- Produces: `PATCH /api/v1/end-customer-billing/settings` aceita `enabled=true` pra qualquer provedor de WhatsApp — comportamento consumido só por front-end/manual, nenhuma outra task deste plano depende disso.

- [ ] **Step 1: Ajustar os testes primeiro**

Em `apps/api/tests/unit/test_end_customer_billing_settings_routes.py`:

**1a.** Em `test_patch_habilitar_sem_pacote_ativo_retorna_400` (linhas 151-161), remover o item `"meta",  # provider do WhatsApp do tenant` do `side_effect`:

```python
def test_patch_habilitar_sem_pacote_ativo_retorna_400(client, session) -> None:
    session.scalar.side_effect = [
        _settings_row(stripe_secret_key_encrypted="cifrado"),  # _get_settings_row
        None,  # checagem de pacote ativo — nenhum cadastrado
    ]

    response = client.patch("/api/v1/end-customer-billing/settings", json={"enabled": True})

    assert response.status_code == 400
    assert "pacote" in response.json()["detail"].lower()
```

**1b.** Em `test_patch_habilitar_com_pacote_ativo_funciona` (linhas 164-174), mesma remoção:

```python
def test_patch_habilitar_com_pacote_ativo_funciona(client, session) -> None:
    session.scalar.side_effect = [
        _settings_row(stripe_secret_key_encrypted="cifrado"),  # _get_settings_row
        uuid.uuid4(),  # checagem de pacote ativo — existe pelo menos 1
    ]

    response = client.patch("/api/v1/end-customer-billing/settings", json={"enabled": True})

    assert response.status_code == 200
    assert response.json()["enabled"] is True
```

**1c.** Remover inteiramente estes 3 testes (linhas 177-215 hoje) — a guarda que eles cobrem deixa de existir, e o comportamento "habilitar funciona independente do provedor" já fica coberto por `test_patch_habilitar_com_pacote_ativo_funciona` acima:
- `test_patch_habilitar_com_whatsapp_zapi_retorna_400`
- `test_patch_habilitar_com_whatsapp_meta_e_pacote_ativo_funciona`
- `test_patch_habilitar_sem_whatsapp_conectado_funciona`

- [ ] **Step 2: Rodar e confirmar a falha esperada**

Run: `cd apps/api && python3 -m pytest tests/unit/test_end_customer_billing_settings_routes.py -v`
Expected: `test_patch_habilitar_sem_pacote_ativo_retorna_400` e `test_patch_habilitar_com_pacote_ativo_funciona` FALHAM (a rota ainda faz a chamada extra de `whatsapp_provider`, então o `side_effect` de 2 itens é consumido fora de ordem — o 2º valor do `side_effect`, que deveria ser o resultado da checagem de pacote, é lido como se fosse o provider).

- [ ] **Step 3: Implementar a remoção**

Em `apps/api/app/api/v1/end_customer_billing.py`, remover `WhatsAppNumber` do bloco de import (linha 19):

```python
from app.models import (
    EndCustomerCreditPackage,
    EndCustomerCreditTransaction,
    EndCustomerSubscription,
    TenantBillingSettings,
)
```

E substituir o bloco das linhas 155-166:

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
```

por:

```python
    if body.enabled is True:
        has_active_package = await session.scalar(
```

(o resto do bloco, `has_active_package is None` → `raise HTTPException(...)`, permanece igual — só a indentação/posição relativa muda, o conteúdo não.)

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd apps/api && python3 -m pytest tests/unit/test_end_customer_billing_settings_routes.py -v`
Expected: todos PASS.

- [ ] **Step 5: Rodar a suíte inteira do `api` (garantir zero regressão, ex: `ruff` acusando import não usado)**

Run: `cd apps/api && python3 -m pytest tests/unit -v && python3 -m ruff check app/api/v1/end_customer_billing.py`
Expected: todos PASS, `ruff` sem achados (import `WhatsAppNumber` removido evita F401).

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/api/v1/end_customer_billing.py apps/api/tests/unit/test_end_customer_billing_settings_routes.py
git commit -m "fix(api): permite habilitar cobrança do cliente final pra tenant Z-API"
```

---

### Task 5: Atualizar CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (linha 461 e linha 548 hoje — confirme o número exato antes de editar, já que tasks anteriores não tocam este arquivo mas o número pode ter deslocado por outras edições concorrentes)

**Interfaces:**
- Consumes: nada de código — só documenta o resultado das Tasks 1-4.
- Produces: nada consumido por outra task.

- [ ] **Step 1: Atualizar a frase sobre o billing gate (linha 461 hoje, dentro de "#### Billing gate determinístico")**

Trocar:

```
Sempre que `tenant_billing_settings.enabled = true`, o funil "sem saldo → escolher pacote → pagar → liberado" nunca passa pelo `agents` (LLM) — é uma máquina de estados determinística, inteiramente no `worker` (`apps/worker/app/billing_gate.py`), usando mensagens nativas do WhatsApp (`interactive`/`list`), zero custo de LLM. `conversations` tem o terceiro estado `billing_gate` + `billing_gate_step`/`billing_gate_retries`/`billing_gate_checkout_url` (ver Modelo de Dados). Não existe mais um mecanismo alternativo — o antigo (embutido no grafo do `agents`) foi removido de vez.
```

por:

```
Sempre que `tenant_billing_settings.enabled = true`, o funil "sem saldo → escolher pacote → pagar → liberado" nunca passa pelo `agents` (LLM) — é uma máquina de estados determinística, inteiramente no `worker` (`apps/worker/app/billing_gate.py`), usando mensagens nativas do WhatsApp (`interactive`/`list` na Meta, `send-option-list` na Z-API — ver "Paridade entre provedores" logo abaixo), zero custo de LLM. `conversations` tem o terceiro estado `billing_gate` + `billing_gate_step`/`billing_gate_retries`/`billing_gate_checkout_url` (ver Modelo de Dados). Não existe mais um mecanismo alternativo — o antigo (embutido no grafo do `agents`) foi removido de vez.
```

- [ ] **Step 2: Inserir uma subseção nova logo depois do parágrafo acima**

Inserir, imediatamente após o parágrafo do Step 1 (antes da linha que hoje começa com "- **Entrada** (`maybe_enter_gate`..."):

```
- ✅ **Paridade entre provedores** (`docs/superpowers/specs/2026-07-29-billing-gate-zapi-paridade-design.md`): o gate funciona igual pra tenants na Meta e na Z-API — `billing_gate.py` roteia o envio por provedor através de `_send_text`/`_send_list`, que decidem a credencial e o formato de payload certos (Meta: `send_interactive_list_message`, com seções nomeadas quando o tenant tem avulso + assinatura; Z-API: `apps/worker/app/clients/zapi.py::send_zapi_option_list`, lista achatada — a Z-API não expõe o conceito de seção, então avulso e assinatura entram na mesma lista, diferenciados só pela `description` de cada opção). A seleção do cliente final é resolvida do mesmo jeito nos dois casos: o texto recebido é comparado contra o nome do pacote (`_resolve_package_by_title`) — a Z-API devolve `listResponseMessage.title` no webhook (`extract_inbound_zapi_message`, `apps/api/app/schemas/whatsapp.py`), que já chega igual ao `list_reply.title` da Meta, sem precisar de nenhuma lógica de resolução por id.
```

- [ ] **Step 3: Remover o bullet de limitação conhecida na seção "Conexão via Z-API"**

Remover a linha (hoje ~548):

```
- ⚠️ **Limitação conhecida — cobrança do cliente final bloqueada pra Z-API**: `PATCH /end-customer-billing/settings` recusa (`400`) `enabled=true` quando `whatsapp_numbers.provider == "zapi"` do tenant — o billing gate determinístico (mensagens `interactive`/`list` nativas da Cloud API da Meta) não tem equivalente testado na Z-API nesta v1; um tenant Z-API que já tinha a cobrança habilitada antes de migrar de provedor não é desligado automaticamente por essa guarda, só fica impedido de reabilitar depois de desligar.
```

Substituir por:

```
- ✅ **Cobrança do cliente final disponível também via Z-API**: a limitação que existia aqui foi resolvida — ver "Paridade entre provedores" na seção "Billing gate determinístico" (Billing / Créditos) pro desenho completo do roteamento por provedor.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: registra paridade Z-API do billing gate determinístico"
```

---

## Self-Review

**Cobertura da spec:**
- Cliente Z-API do worker (`send_zapi_option_list`) → Task 1.
- Roteamento por provedor em `billing_gate.py` (`_send_text`/`_send_list`, remoção do parâmetro `access_token`) → Task 3.
- Lista achatada pra Z-API (`_split_by_kind`/`_packages_to_flat_options`) → Task 3.
- Parsing de `listResponseMessage` no webhook → Task 2.
- Remoção das 2 guardas → Task 3 (`maybe_enter_gate`) e Task 4 (`end_customer_billing.py`).
- Atualização do CLAUDE.md → Task 5.
- Testes cobrindo os 2 testes existentes invertidos + novos → Tasks 3 e 4.

Nenhum item da spec ficou sem task correspondente.

**Placeholders:** nenhum "TBD"/"implementar depois" — todo código é completo e executável como escrito.

**Consistência de tipos/nomes:** `send_zapi_option_list` (Task 1) é chamado com os mesmos nomes de parâmetro (`instance_id`, `token`, `client_token`, `to`, `message`, `title`, `button_label`, `options`) tanto na Task 1 (testes do cliente) quanto na Task 3 (`_send_list`). `_packages_to_flat_options`/`_split_by_kind` (Task 3) usam a mesma assinatura em toda parte que aparecem. `extract_inbound_zapi_message` (Task 2) mantém a assinatura pública inalterada.
