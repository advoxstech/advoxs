# Migração da cobrança do cliente final para Stripe Connect — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Substituir o modelo "tenant cola a própria secret key/webhook secret da Stripe" por Stripe Connect (Accounts v2, Direct charges) — o tenant configura tudo dentro do painel Advoxs, sem sair da plataforma, e a Advoxs nunca vê/guarda dado sensível (CPF/CNPJ, conta bancária).

**Architecture:** Uma coluna `billing_provider` em `tenant_billing_settings` decide, por tenant, se o checkout/webhook do cliente final passa pelo caminho antigo (`standalone`) ou pelo novo (`connect`). O onboarding roda embutido no painel via Connect.js + Account Sessions (Stripe cria a UI de KYC dentro de um iframe no próprio domínio da Advoxs). O checkout do cliente final passa a ser uma Direct charge na conta conectada do tenant, sem comissão da Advoxs. Um único endpoint de webhook novo, com um único segredo de plataforma, atende todas as contas conectadas.

**Tech Stack:** FastAPI + Python 3.12 + `stripe-python` 15.3.0 (já travado no `apps/api/uv.lock`, compatível com Accounts v2) + Alembic; Next.js 15 + `@stripe/connect-js` no `apps/web`.

## Global Constraints

- Spec de referência: `docs/superpowers/specs/2026-07-24-stripe-connect-cobranca-cliente-final-design.md`.
- **Sem comissão da Advoxs** em nenhum checkout do cliente final (nem no modelo `standalone` já existente, nem no novo `connect`) — nunca usar `application_fee_amount`.
- **Nenhum dado de CPF/CNPJ, endereço ou conta bancária do tenant é armazenado no Postgres da Advoxs** — esses dados só existem dentro da conta conectada na Stripe.
- Padrão de conta: **Accounts v2** (`dashboard="full"`, `defaults.responsibilities.fees_collector="stripe"`, `defaults.responsibilities.losses_collector="stripe"`) — nunca `type="custom"`/`"express"`/`"standard"` (API v1 depreciada).
- Capabilities solicitadas na criação da conta: `card_payments` **e** `pix` (decisão confirmada — mercado de cliente final brasileiro usa Pix).
- Onboarding **sempre embutido** (Connect.js + Account Session) — nunca redirect pra fora do domínio da Advoxs, nunca formulário próprio coletando PF/CNPJ (isso é responsabilidade do componente da Stripe).
- **Nenhuma outra tabela do mecanismo de cobrança do cliente final muda**: `end_customer_credit_packages`, `end_customer_balances`, `end_customer_credit_transactions`, `conversations.billing_gate_*` ficam intactas.
- `billing_provider="standalone"` é um estado **transitório** — tenants novos só configuram via `connect`; tenants já em `standalone` continuam funcionando, mas o objetivo final é todo tenant migrar (fora de escopo desta entrega: forçar/depreciar o `standalone`, ver spec).
- Chave Stripe usada pras chamadas de Connect (`stripe.v2.core.Account.create`, `stripe.AccountSession.create`) é **própria e separada** (`settings.stripe_connect_secret_key`) — nunca reaproveitar `settings.stripe_secret_key` (escopo restrito só a "Checkout Sessions: Write", usado pelo billing tenant→Advoxs) nem a secret key cifrada de nenhum tenant.
- 3 itens desta entrega dependem da API real da Stripe e **não têm 100% de certeza documental no momento em que este plano foi escrito** — cada task correspondente traz um passo explícito de "confirmar antes de codificar" com a URL/fonte a consultar. Não pule esse passo nem assuma o código de exemplo deste plano como definitivo sem essa confirmação.

---

## Task 1: Modelo de dados — `billing_provider`, `stripe_account_id`, `stripe_account_status`

**Files:**
- Create: `apps/api/alembic/versions/0021_stripe_connect_cobranca_cliente_final.py`
- Modify: `apps/api/app/models/end_customer_billing.py:22-50` (classe `TenantBillingSettings`)
- Modify: `apps/api/app/schemas/end_customer_billing.py:7-18` (classe `TenantBillingSettingsOut`)
- Test: `apps/api/tests/unit/test_end_customer_billing_settings_routes.py`

**Interfaces:**
- Produces: `TenantBillingSettings.billing_provider: str` (`"standalone"` | `"connect"`, nunca `None`), `TenantBillingSettings.stripe_account_id: str | None`, `TenantBillingSettings.stripe_account_status: str | None` (`"not_started"` | `"onboarding"` | `"active"` | `None`). `TenantBillingSettingsOut` ganha os mesmos 3 campos.

- [ ] **Step 1: Escrever a migration**

```python
"""Stripe Connect (Accounts v2) para a cobrança do cliente final — tenant
deixa de colar secret key/webhook secret da própria conta Stripe; passa a
configurar tudo via onboarding embutido no painel Advoxs. billing_provider
decide, por tenant, qual caminho o checkout/webhook seguem — ver
docs/superpowers/specs/2026-07-24-stripe-connect-cobranca-cliente-final-design.md.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-24
"""

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_billing_settings",
        sa.Column(
            "billing_provider",
            sa.String(),
            server_default=sa.text("'standalone'"),
            nullable=False,
        ),
    )
    op.add_column(
        "tenant_billing_settings",
        sa.Column("stripe_account_id", sa.String(), nullable=True),
    )
    op.add_column(
        "tenant_billing_settings",
        sa.Column("stripe_account_status", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_billing_settings", "stripe_account_status")
    op.drop_column("tenant_billing_settings", "stripe_account_id")
    op.drop_column("tenant_billing_settings", "billing_provider")
```

- [ ] **Step 2: Rodar a migration contra o Postgres local e confirmar**

Run: `cd apps/api && uv run alembic upgrade head`
Expected: sem erro; `uv run alembic current` mostra `0021`.

- [ ] **Step 3: Atualizar o model**

Em `apps/api/app/models/end_customer_billing.py`, adicionar os 3 campos à classe `TenantBillingSettings` (depois de `stripe_webhook_secret_encrypted`, antes de `end_customer_tokens_per_credit`):

```python
    billing_provider: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'standalone'")
    )
    stripe_account_id: Mapped[str | None] = mapped_column(String)
    stripe_account_status: Mapped[str | None] = mapped_column(String)
```

- [ ] **Step 4: Escrever o teste que falha — `TenantBillingSettingsOut` expõe os campos novos**

Em `apps/api/tests/unit/test_end_customer_billing_settings_routes.py`, adicionar:

```python
def test_get_sem_configuracao_retorna_billing_provider_standalone_por_default(
    client, session
) -> None:
    session.scalar.return_value = None

    response = client.get("/api/v1/end-customer-billing/settings")

    body = response.json()
    assert body["billing_provider"] == "standalone"
    assert body["stripe_account_id"] is None
    assert body["stripe_account_status"] is None


def test_get_com_conta_connect_retorna_billing_provider_e_status(client, session) -> None:
    session.scalar.return_value = _settings_row(
        billing_provider="connect",
        stripe_account_id="acct_123",
        stripe_account_status="active",
    )

    response = client.get("/api/v1/end-customer-billing/settings")

    body = response.json()
    assert body["billing_provider"] == "connect"
    assert body["stripe_account_id"] == "acct_123"
    assert body["stripe_account_status"] == "active"
```

Também adicionar `billing_provider="standalone"` ao dict base de `_settings_row` (topo do arquivo, função `_settings_row`), já que o novo campo obrigatório em `TenantBillingSettingsOut` vai quebrar toda linha que monta o objeto de resposta a partir de um `SimpleNamespace` sem esse atributo:

```python
def _settings_row(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        enabled=False,
        billing_mode="credits",
        billing_provider="standalone",
        stripe_account_id=None,
        stripe_account_status=None,
        stripe_secret_key_encrypted=None,
        stripe_webhook_secret_encrypted=None,
        end_customer_tokens_per_credit=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row
```

- [ ] **Step 5: Rodar os testes e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_settings_routes.py -v`
Expected: as 2 novas asserções falham com `KeyError: 'billing_provider'` (campo ainda não existe na resposta) — as demais continuam passando.

- [ ] **Step 6: Atualizar o schema `TenantBillingSettingsOut`**

Em `apps/api/app/schemas/end_customer_billing.py`, adicionar à classe `TenantBillingSettingsOut` (depois de `billing_mode`):

```python
    billing_provider: str
    stripe_account_id: str | None
    stripe_account_status: str | None
```

- [ ] **Step 7: Atualizar `_to_settings_out` em `apps/api/app/api/v1/end_customer_billing.py`**

Nos dois `return TenantBillingSettingsOut(...)` (linhas 43-51 e 52-60 do arquivo atual), adicionar os 3 campos — no ramo `settings_row is None`:

```python
            billing_provider="standalone",
            stripe_account_id=None,
            stripe_account_status=None,
```

E no ramo com `settings_row`:

```python
        billing_provider=settings_row.billing_provider,
        stripe_account_id=settings_row.stripe_account_id,
        stripe_account_status=settings_row.stripe_account_status,
```

- [ ] **Step 8: Rodar os testes de novo e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_settings_routes.py -v`
Expected: todos os testes passam (as 2 novas + as pré-existentes).

- [ ] **Step 9: Commit**

```bash
cd apps/api
git add alembic/versions/0021_stripe_connect_cobranca_cliente_final.py app/models/end_customer_billing.py app/schemas/end_customer_billing.py app/api/v1/end_customer_billing.py tests/unit/test_end_customer_billing_settings_routes.py
git commit -m "feat(api): adiciona billing_provider/stripe_account_id/stripe_account_status"
```

---

## Task 2: Config + serviço de criação/atualização da conta conectada

**Files:**
- Create: `apps/api/app/services/stripe_connect.py`
- Modify: `apps/api/app/core/config.py:50-60`
- Modify: `.env.example` (raiz do repo)
- Test: `apps/api/tests/unit/test_stripe_connect_service.py`

**Interfaces:**
- Consumes: `TenantBillingSettings` (Task 1) com `billing_provider`, `stripe_account_id`, `stripe_account_status`.
- Produces: `async def create_or_refresh_connect_account(session: AsyncSession, tenant_id: uuid.UUID) -> str` (devolve o `client_secret` da Account Session), classe de exceção `ConnectApiError(Exception)`.

- [ ] **Step 1 — CONFIRMAR ANTES DE CODIFICAR: forma exata da chamada v2 no `stripe-python` 15.3.0**

Este é um dos 3 itens que a spec marca como "a confirmar durante a implementação". Antes de escrever qualquer teste, usar a ferramenta de fetch de documentação (ou `python3 -c "import stripe; help(stripe.v2.core.Account.create)"` dentro do venv do `apps/api`, se disponível) para confirmar, contra a versão 15.3.0 instalada:
1. O módulo/atributo exato pra criar a conta v2 (referência de partida: `stripe.v2.core.Account.create(...)` — mas `stripe-python` também expõe um padrão via `stripe.StripeClient(api_key).v2.core.accounts.create(...)`; confirmar qual dos dois existe e funciona nesta versão).
2. Se a chamada aceita `api_key=` como kwarg explícito por chamada (padrão que este projeto usa em toda outra integração Stripe — nunca `stripe.api_key` global) ou se a v2 exige `stripe.StripeClient(api_key=...)` instanciado por chamada.
3. O nome exato do parâmetro pra criar a **Account Session** (referência de partida: `stripe.AccountSession.create(api_key=..., account=..., components={"account_onboarding": {"enabled": True}})`).

Documentar a confirmação (URL consultada + trecho relevante) no relatório da task antes de prosseguir. Se a chamada real divergir do código de partida abaixo, ajustar o código e os mocks do teste de acordo — a estrutura da função (criar conta só se `stripe_account_id is None`, sempre criar uma Account Session nova, devolver `client_secret`) não muda.

- [ ] **Step 2: Adicionar as novas settings**

Em `apps/api/app/core/config.py`, depois da linha 58 (`tenant_stripe_key_encryption_key: str = ""`):

```python
    # Stripe Connect (Accounts v2) — cobrança do cliente final, chave própria
    # separada de stripe_secret_key (escopo restrito: Connected Accounts +
    # Account Sessions, nunca Checkout Sessions do billing tenant->Advoxs).
    stripe_connect_secret_key: str = ""
    # Segredo único de webhook pra TODAS as contas conectadas — não é por
    # tenant, é da plataforma (ver apps/api/app/api/v1/webhooks/stripe_connect.py).
    stripe_connect_webhook_secret: str = ""
```

Em `.env.example` (raiz do repo), depois da linha `TENANT_STRIPE_KEY_ENCRYPTION_KEY=` (verificar posição exata com `grep -n TENANT_STRIPE .env.example`):

```
STRIPE_CONNECT_SECRET_KEY=
STRIPE_CONNECT_WEBHOOK_SECRET=
```

- [ ] **Step 3: Escrever o teste que falha**

Criar `apps/api/tests/unit/test_stripe_connect_service.py`:

```python
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.stripe_connect as stripe_connect_module
from app.services.stripe_connect import ConnectApiError, create_or_refresh_connect_account

TENANT_ID = uuid.uuid4()


@pytest.fixture
def session():
    mock = AsyncMock()
    mock.add = MagicMock()
    return mock


@pytest.mark.asyncio
async def test_cria_conta_quando_tenant_nao_tem_stripe_account_id(session, monkeypatch):
    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        billing_provider="connect",
        stripe_account_id=None,
        stripe_account_status=None,
    )
    session.scalar.return_value = row
    created_account = SimpleNamespace(id="acct_novo")
    monkeypatch.setattr(
        stripe_connect_module,
        "_create_stripe_account",
        AsyncMock(return_value=created_account),
    )
    monkeypatch.setattr(
        stripe_connect_module,
        "_create_account_session",
        AsyncMock(return_value=SimpleNamespace(client_secret="secret_abc")),
    )

    client_secret = await create_or_refresh_connect_account(session, TENANT_ID)

    assert client_secret == "secret_abc"
    assert row.stripe_account_id == "acct_novo"
    assert row.stripe_account_status == "onboarding"
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_nao_recria_conta_quando_tenant_ja_tem_stripe_account_id(session, monkeypatch):
    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        billing_provider="connect",
        stripe_account_id="acct_existente",
        stripe_account_status="active",
    )
    session.scalar.return_value = row
    create_account_mock = AsyncMock()
    monkeypatch.setattr(stripe_connect_module, "_create_stripe_account", create_account_mock)
    monkeypatch.setattr(
        stripe_connect_module,
        "_create_account_session",
        AsyncMock(return_value=SimpleNamespace(client_secret="secret_novo")),
    )

    client_secret = await create_or_refresh_connect_account(session, TENANT_ID)

    assert client_secret == "secret_novo"
    create_account_mock.assert_not_awaited()
    assert row.stripe_account_id == "acct_existente"


@pytest.mark.asyncio
async def test_cria_linha_de_settings_quando_tenant_nunca_configurou_nada(session, monkeypatch):
    session.scalar.return_value = None
    added = []
    session.add = MagicMock(side_effect=lambda obj: added.append(obj))
    monkeypatch.setattr(
        stripe_connect_module,
        "_create_stripe_account",
        AsyncMock(return_value=SimpleNamespace(id="acct_novo")),
    )
    monkeypatch.setattr(
        stripe_connect_module,
        "_create_account_session",
        AsyncMock(return_value=SimpleNamespace(client_secret="secret_abc")),
    )

    await create_or_refresh_connect_account(session, TENANT_ID)

    assert len(added) == 1
    created_row = added[0]
    assert created_row.tenant_id == TENANT_ID
    assert created_row.billing_provider == "connect"


@pytest.mark.asyncio
async def test_erro_da_stripe_ao_criar_conta_levanta_connect_api_error(session, monkeypatch):
    import stripe

    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        billing_provider="connect",
        stripe_account_id=None,
        stripe_account_status=None,
    )
    session.scalar.return_value = row

    async def _raise(*args, **kwargs):
        raise stripe.error.StripeError("falhou")

    monkeypatch.setattr(stripe_connect_module, "_create_stripe_account", _raise)

    with pytest.raises(ConnectApiError):
        await create_or_refresh_connect_account(session, TENANT_ID)
```

- [ ] **Step 4: Rodar o teste e confirmar que falha**

Run: `cd apps/api && uv run pytest tests/unit/test_stripe_connect_service.py -v`
Expected: `ModuleNotFoundError: No module named 'app.services.stripe_connect'`.

- [ ] **Step 5: Implementar o serviço**

Criar `apps/api/app/services/stripe_connect.py` (ajustar `_create_stripe_account`/`_create_account_session` conforme a confirmação do Step 1 se a sintaxe divergir):

```python
"""Onboarding da conta conectada (Stripe Connect, Accounts v2) do tenant —
cobrança do cliente final. Substitui, para tenants em billing_provider=
"connect", o modelo antigo de colar secret key/webhook secret
(billing_provider="standalone", ver app/services/end_customer_billing.py).

Usa uma chave restrita própria (settings.stripe_connect_secret_key, escopo
Connected Accounts + Account Sessions) — nunca a mesma chave usada pelo
billing tenant->Advoxs (settings.stripe_secret_key, escopo só Checkout
Sessions) nem a secret key cifrada de nenhum tenant.
"""

import asyncio
import logging
import uuid

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import TenantBillingSettings

logger = logging.getLogger(__name__)


class ConnectApiError(Exception):
    """Falha ao criar/atualizar a conta conectada ou a Account Session."""


async def _create_stripe_account() -> stripe.v2.core.Account:
    return await asyncio.to_thread(
        stripe.v2.core.Account.create,
        api_key=settings.stripe_connect_secret_key,
        identity={"country": "BR"},
        dashboard="full",
        configuration={
            "merchant": {
                "capabilities": {
                    "card_payments": {"requested": True},
                    "pix": {"requested": True},
                }
            }
        },
        defaults={
            "responsibilities": {
                "fees_collector": "stripe",
                "losses_collector": "stripe",
            }
        },
    )


async def _create_account_session(stripe_account_id: str) -> stripe.AccountSession:
    return await asyncio.to_thread(
        stripe.AccountSession.create,
        api_key=settings.stripe_connect_secret_key,
        account=stripe_account_id,
        components={"account_onboarding": {"enabled": True}},
    )


async def create_or_refresh_connect_account(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    """Cria a conta v2 na primeira chamada; chamadas seguintes só geram uma
    nova Account Session pra conta já existente. Devolve o client_secret da
    Account Session, usado pelo frontend pra inicializar o Connect.js."""
    row = await session.scalar(
        select(TenantBillingSettings).where(TenantBillingSettings.tenant_id == tenant_id)
    )
    if row is None:
        row = TenantBillingSettings(
            tenant_id=tenant_id, enabled=False, billing_mode="credits", billing_provider="connect"
        )
        session.add(row)
        await session.flush()

    if row.stripe_account_id is None:
        try:
            account = await _create_stripe_account()
        except stripe.error.StripeError as exc:
            logger.error("Falha ao criar conta conectada | tenant=%s erro=%s", tenant_id, exc)
            raise ConnectApiError("Falha ao iniciar a configuração de pagamentos") from exc

        row.stripe_account_id = account.id
        row.stripe_account_status = "onboarding"
        row.billing_provider = "connect"
        await session.commit()

    try:
        account_session = await _create_account_session(row.stripe_account_id)
    except stripe.error.StripeError as exc:
        logger.error("Falha ao criar Account Session | tenant=%s erro=%s", tenant_id, exc)
        raise ConnectApiError("Falha ao iniciar a configuração de pagamentos") from exc

    return account_session.client_secret
```

- [ ] **Step 6: Rodar o teste e confirmar que passa**

Run: `cd apps/api && uv run pytest tests/unit/test_stripe_connect_service.py -v`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
cd apps/api
git add app/services/stripe_connect.py app/core/config.py tests/unit/test_stripe_connect_service.py
cd ../..
git add .env.example
git commit -m "feat(api): serviço de criação/atualização da conta Stripe Connect do tenant"
```

---

## Task 3: Rotas — `connect-account`, `account-status`, e bloqueio de `standalone` do zero

**Files:**
- Modify: `apps/api/app/api/v1/end_customer_billing.py`
- Modify: `apps/api/app/schemas/end_customer_billing.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_settings_routes.py`

**Interfaces:**
- Consumes: `create_or_refresh_connect_account` (Task 2), `TenantBillingSettingsOut` (Task 1).
- Produces: `POST /api/v1/end-customer-billing/connect-account` → `ConnectAccountSessionOut {client_secret: str}`. `PATCH /api/v1/end-customer-billing/settings` passa a rejeitar (400) criar uma linha nova com `stripe_secret_key` quando não existe linha ainda.

- [ ] **Step 1: Escrever o teste que falha — bloqueio de standalone do zero**

Em `apps/api/tests/unit/test_end_customer_billing_settings_routes.py`, adicionar:

```python
def test_patch_com_secret_key_sem_linha_existente_retorna_400(client, session) -> None:
    """Tenant novo não pode configurar standalone do zero — só via Connect
    (POST /connect-account). Tenants que já têm uma linha (billing_provider=
    "standalone", já configurados antes desta feature) continuam podendo
    fazer PATCH normalmente — ver test_patch_secret_key_tokens_e_enabled_juntos_funciona."""
    session.scalar.return_value = None

    response = client.patch(
        "/api/v1/end-customer-billing/settings",
        json={"stripe_secret_key": "sk_test_123"},
    )

    assert response.status_code == 400
    assert "Connect" in response.json()["detail"]
```

E ajustar `test_patch_cria_registro_quando_nao_existe` (já existente) — esse teste hoje espera que um `PATCH` com `stripe_secret_key` numa linha inexistente crie a linha; com o bloqueio novo isso deixa de ser válido. Substituir o corpo da chamada por um PATCH que **não** inclui `stripe_secret_key`, só `end_customer_tokens_per_credit`, mantendo a asserção de que a linha é criada (o campo `stripe_secret_key_encrypted` some da asserção):

```python
def test_patch_cria_registro_quando_nao_existe(client, session, monkeypatch) -> None:
    session.scalar.return_value = None
    added = []
    session.add = MagicMock(side_effect=lambda obj: added.append(obj))

    response = client.patch(
        "/api/v1/end-customer-billing/settings",
        json={"end_customer_tokens_per_credit": 300},
    )

    assert response.status_code == 200
    assert len(added) == 1
    created = added[0]
    assert created.tenant_id == TENANT_ID
    assert created.billing_provider == "standalone"
    assert created.end_customer_tokens_per_credit == 300
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_settings_routes.py -v`
Expected: `test_patch_com_secret_key_sem_linha_existente_retorna_400` falha (retorna 200 hoje, sem bloqueio); `test_patch_cria_registro_quando_nao_existe` (versão nova) passa igual (comportamento já correto pra esse caso — só a asserção mudou), confirmar que ainda passa antes de seguir.

- [ ] **Step 3: Implementar o bloqueio em `update_settings`**

Em `apps/api/app/api/v1/end_customer_billing.py`, modificar a função `update_settings` (linhas 79-109) — adicionar a checagem logo após resolver `row`:

```python
@router.patch("/settings")
async def update_settings(
    body: TenantBillingSettingsUpdate,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> TenantBillingSettingsOut:
    row = await _get_settings_row(ctx, session)
    if row is None:
        if body.stripe_secret_key is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Configuração via secret key não está mais disponível pra tenants novos — "
                    "use POST /end-customer-billing/connect-account (Stripe Connect)."
                ),
            )
        row = TenantBillingSettings(
            tenant_id=ctx.tenant_id,
            enabled=False,
            billing_mode="credits",
            billing_provider="standalone",
        )
        session.add(row)

    if body.stripe_secret_key is not None:
        row.stripe_secret_key_encrypted = encrypt_tenant_secret(body.stripe_secret_key)
    if body.stripe_webhook_secret is not None:
        row.stripe_webhook_secret_encrypted = encrypt_tenant_secret(body.stripe_webhook_secret)
    if body.end_customer_tokens_per_credit is not None:
        row.end_customer_tokens_per_credit = body.end_customer_tokens_per_credit

    if body.enabled is True and row.stripe_secret_key_encrypted is None and row.stripe_account_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Configure a secret key da Stripe (ou conclua o onboarding Connect) antes de ativar a cobrança",
        )
    if body.enabled is not None:
        row.enabled = body.enabled

    await session.commit()
    return _to_settings_out(ctx.tenant_id, row)
```

Nota: a linha `billing_provider="standalone"` explícita na criação da linha nova é redundante com o `server_default` da coluna (Task 1), mas deixá-la explícita documenta a intenção no código, mesmo padrão já usado nas outras 2 colunas explícitas dessa mesma linha (`enabled`, `billing_mode`).

A condição de `enabled=True` também foi ampliada pra aceitar `row.stripe_account_id is not None` como alternativa válida à secret key — sem isso, um tenant que migrou pra Connect e nunca teve `stripe_secret_key_encrypted` preenchido nunca conseguiria ativar a cobrança via este mesmo endpoint (ainda que `enabled` deva, na prática, ser ativado pelo fluxo Connect — ver Step 4 abaixo — vale deixar este PATCH consistente).

- [ ] **Step 4: Rodar os testes de novo e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_settings_routes.py -v`
Expected: todos passam.

- [ ] **Step 5: Escrever o teste que falha — `POST /connect-account`**

Criar `apps/api/tests/unit/test_connect_account_routes.py`:

```python
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app

TENANT_ID = uuid.uuid4()


@pytest.fixture
def session():
    return AsyncMock()


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


def test_post_connect_account_devolve_client_secret(client, session, monkeypatch):
    import app.api.v1.end_customer_billing as routes_module

    monkeypatch.setattr(
        routes_module, "create_or_refresh_connect_account", AsyncMock(return_value="secret_abc")
    )

    response = client.post("/api/v1/end-customer-billing/connect-account")

    assert response.status_code == 200
    assert response.json()["client_secret"] == "secret_abc"


def test_post_connect_account_erro_da_stripe_retorna_502(client, session, monkeypatch):
    import app.api.v1.end_customer_billing as routes_module
    from app.services.stripe_connect import ConnectApiError

    async def _raise(*args, **kwargs):
        raise ConnectApiError("falhou")

    monkeypatch.setattr(routes_module, "create_or_refresh_connect_account", _raise)

    response = client.post("/api/v1/end-customer-billing/connect-account")

    assert response.status_code == 502


def test_sem_token_retorna_401_no_connect_account():
    response = TestClient(app).post("/api/v1/end-customer-billing/connect-account")
    assert response.status_code == 401
```

- [ ] **Step 6: Rodar o teste e confirmar que falha**

Run: `cd apps/api && uv run pytest tests/unit/test_connect_account_routes.py -v`
Expected: `AttributeError` — rota `/connect-account` não existe (404) e/ou `create_or_refresh_connect_account` não está importado em `end_customer_billing.py`.

- [ ] **Step 7: Implementar a rota**

Em `apps/api/app/schemas/end_customer_billing.py`, adicionar:

```python
class ConnectAccountSessionOut(BaseModel):
    client_secret: str
```

Em `apps/api/app/api/v1/end_customer_billing.py`, adicionar ao bloco de imports:

```python
from app.schemas.end_customer_billing import (
    ConnectAccountSessionOut,
    EndCustomerCreditPackageIn,
    EndCustomerCreditPackageOut,
    EndCustomerCreditPackageUpdate,
    EndCustomerSummaryOut,
    TenantBillingSettingsOut,
    TenantBillingSettingsUpdate,
)
from app.services.stripe_connect import ConnectApiError, create_or_refresh_connect_account
```

E a rota nova, depois de `update_settings`:

```python
@router.post("/connect-account")
async def connect_account(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ConnectAccountSessionOut:
    try:
        client_secret = await create_or_refresh_connect_account(session, ctx.tenant_id)
    except ConnectApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return ConnectAccountSessionOut(client_secret=client_secret)
```

- [ ] **Step 8: Rodar o teste e confirmar que passa**

Run: `cd apps/api && uv run pytest tests/unit/test_connect_account_routes.py -v`
Expected: 3 passed.

- [ ] **Step 9: Commit**

```bash
cd apps/api
git add app/api/v1/end_customer_billing.py app/schemas/end_customer_billing.py tests/unit/test_end_customer_billing_settings_routes.py tests/unit/test_connect_account_routes.py
git commit -m "feat(api): rota de onboarding Connect e bloqueio de standalone do zero"
```

---

## Task 4: Webhook dedicado a Connect

**Files:**
- Create: `apps/api/app/api/v1/webhooks/stripe_connect.py`
- Modify: `apps/api/app/api/v1/router.py`
- Test: `apps/api/tests/unit/test_stripe_connect_webhook.py`

**Interfaces:**
- Consumes: `process_end_customer_checkout_completed` (já existe, `apps/api/app/services/end_customer_billing.py`), `TenantBillingSettings.stripe_account_id`.
- Produces: `POST /api/v1/webhooks/stripe/connect` — endpoint único pra todas as contas conectadas.

- [ ] **Step 1 — CONFIRMAR ANTES DE CODIFICAR: nome do evento de status/capability em Accounts v2**

Segundo item marcado na spec como "a confirmar durante a implementação". Confirmar, contra a doc atual da Stripe (Connect webhooks / Accounts v2 change events), se o evento que sinaliza mudança de capability/status de uma conta v2 ainda é `account.updated` (padrão v1, usado nesta implementação de partida) ou se há um nome/formato v2 equivalente. Documentar a fonte consultada no relatório da task. Se o nome divergir, só a constante `_STATUS_EVENT_TYPE` abaixo precisa mudar — o resto da lógica do handler não depende do nome exato.

- [ ] **Step 2: Escrever o teste que falha**

Criar `apps/api/tests/unit/test_stripe_connect_webhook.py`:

```python
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.webhooks.stripe_connect as webhook_module
from app.core.db import get_system_session
from app.main import app

TENANT_ID = uuid.uuid4()
ACCOUNT_ID = "acct_123"


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture
def client(session):
    async def override_session():
        yield session

    app.dependency_overrides[get_system_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_assinatura_invalida_retorna_400(client, session, monkeypatch):
    import stripe

    def _raise(*args, **kwargs):
        raise stripe.error.SignatureVerificationError("inválida", "sig")

    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", _raise)

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-invalida"},
    )

    assert response.status_code == 400


def test_checkout_completed_resolve_tenant_pelo_stripe_account_id(client, session, monkeypatch):
    session.scalar = AsyncMock(
        return_value=SimpleNamespace(tenant_id=TENANT_ID, stripe_account_id=ACCOUNT_ID)
    )
    event = {
        "type": "checkout.session.completed",
        "account": ACCOUNT_ID,
        "data": {"object": {"id": "cs_1"}},
    }
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process = AsyncMock()
    monkeypatch.setattr(webhook_module, "process_end_customer_checkout_completed", process)

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process.assert_awaited_once()
    assert process.await_args.args[1] == TENANT_ID


def test_evento_sem_tenant_resolvido_e_ignorado(client, session, monkeypatch):
    session.scalar = AsyncMock(return_value=None)
    event = {
        "type": "checkout.session.completed",
        "account": "acct_desconhecido",
        "data": {"object": {"id": "cs_1"}},
    }
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process = AsyncMock()
    monkeypatch.setattr(webhook_module, "process_end_customer_checkout_completed", process)

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process.assert_not_awaited()


def test_evento_de_status_atualiza_stripe_account_status(client, session, monkeypatch):
    row = SimpleNamespace(tenant_id=TENANT_ID, stripe_account_id=ACCOUNT_ID, stripe_account_status=None)
    session.scalar = AsyncMock(return_value=row)
    event = {
        "type": webhook_module._STATUS_EVENT_TYPE,
        "account": ACCOUNT_ID,
        "data": {"object": {"id": ACCOUNT_ID}},
    }
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    monkeypatch.setattr(webhook_module, "_resolve_account_status", AsyncMock(return_value="active"))

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    assert row.stripe_account_status == "active"
    session.commit.assert_awaited()
```

- [ ] **Step 3: Rodar o teste e confirmar que falha**

Run: `cd apps/api && uv run pytest tests/unit/test_stripe_connect_webhook.py -v`
Expected: `ModuleNotFoundError: No module named 'app.api.v1.webhooks.stripe_connect'`.

- [ ] **Step 4: Implementar o webhook**

Criar `apps/api/app/api/v1/webhooks/stripe_connect.py` (ajustar `_STATUS_EVENT_TYPE`/`_resolve_account_status` conforme a confirmação do Step 1 se necessário):

```python
"""Webhook único de Stripe Connect — escuta eventos de TODAS as contas
conectadas dos tenants (cobrança do cliente final, billing_provider=
"connect"). Diferente de webhooks/stripe_tenant.py (modelo standalone antigo,
1 endpoint + 1 secret por tenant): aqui é 1 endpoint + 1 secret pra
plataforma inteira, tenant resolvido via event["account"].
"""

import logging

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_system_session
from app.models import TenantBillingSettings
from app.services.end_customer_billing import process_end_customer_checkout_completed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/stripe/connect", tags=["webhooks"])

# Nome do evento confirmado no Step 1 desta task — ver docstring da task no
# plano de implementação se precisar revisar a fonte consultada.
_STATUS_EVENT_TYPE = "account.updated"

_ASSINATURA_INVALIDA = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST, detail="Assinatura inválida"
)


async def _resolve_account_status(account_payload: dict) -> str:
    """Deriva "not_started"/"onboarding"/"active" a partir da capability
    card_payments no payload do evento — ativo é o que libera o billing gate
    determinístico a considerar esse tenant configurado."""
    capabilities = account_payload.get("configuration", {}).get("merchant", {}).get(
        "capabilities", {}
    )
    card_payments = capabilities.get("card_payments", {})
    if card_payments.get("status") == "active":
        return "active"
    return "onboarding"


@router.post("")
async def receive_connect_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    session: AsyncSession = Depends(get_system_session),
) -> dict:
    raw_body = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            raw_body, stripe_signature, settings.stripe_connect_webhook_secret
        )
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        logger.warning("Assinatura de webhook Connect inválida | erro=%s", exc)
        raise _ASSINATURA_INVALIDA

    account_id = event.get("account")
    if not account_id:
        return {"status": "ok"}

    billing_settings = await session.scalar(
        select(TenantBillingSettings).where(TenantBillingSettings.stripe_account_id == account_id)
    )
    if billing_settings is None:
        logger.warning("Evento Connect de conta desconhecida | account=%s", account_id)
        return {"status": "ok"}

    if event["type"] == "checkout.session.completed":
        await process_end_customer_checkout_completed(
            session, billing_settings.tenant_id, event["data"]["object"]
        )
    elif event["type"] == _STATUS_EVENT_TYPE:
        billing_settings.stripe_account_status = await _resolve_account_status(
            event["data"]["object"]
        )
        await session.commit()

    return {"status": "ok"}
```

- [ ] **Step 5: Registrar o router**

Em `apps/api/app/api/v1/router.py`, adicionar o import (junto dos outros webhooks, ordem alfabética):

```python
from app.api.v1.webhooks.stripe_connect import router as stripe_connect_webhook_router
```

E o `include_router` (junto dos outros, antes de `stripe_tenant_webhook_router` por ordem alfabética):

```python
api_router.include_router(stripe_connect_webhook_router)
```

- [ ] **Step 6: Rodar o teste e confirmar que passa**

Run: `cd apps/api && uv run pytest tests/unit/test_stripe_connect_webhook.py -v`
Expected: 4 passed.

- [ ] **Step 7: Rodar a suíte completa do `apps/api` pra checar regressão**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Expected: sem falhas novas, ruff limpo.

- [ ] **Step 8: Commit**

```bash
cd apps/api
git add app/api/v1/webhooks/stripe_connect.py app/api/v1/router.py tests/unit/test_stripe_connect_webhook.py
git commit -m "feat(api): webhook único de Stripe Connect (todas as contas conectadas)"
```

---

## Task 5: Checkout do cliente final — Direct charge para tenants `connect`

**Files:**
- Modify: `apps/api/app/services/end_customer_billing.py:50-105` (`create_end_customer_checkout_session`)
- Test: `apps/api/tests/unit/test_end_customer_billing_service.py`

**Interfaces:**
- Consumes: `TenantBillingSettings.billing_provider`/`stripe_account_id` (Task 1), `settings.stripe_connect_secret_key` (Task 2).
- Produces: `create_end_customer_checkout_session` mantém a mesma assinatura (`session, tenant_id, contact_phone_number, package_id) -> str`); ramifica internamente por `billing_provider`.

- [ ] **Step 1 — CONFIRMAR ANTES DE CODIFICAR: parâmetro exato de Direct charge na Checkout Session**

Terceiro item marcado na spec como "a confirmar durante a implementação". Confirmar, contra a doc atual da Stripe (Direct charges + Checkout Sessions, Accounts v2), o parâmetro/mecanismo exato pra fazer uma Checkout Session cobrar como Direct charge numa conta conectada — referência de partida (padrão histórico de direct charge): passar `stripe_account=<acct_id>` como kwarg de nível de requisição no SDK Python (equivalente ao header `Stripe-Account`), **não** um campo dentro do corpo JSON da sessão. Documentar a fonte consultada no relatório da task; se divergir, só a chamada dentro de `_create_connect_checkout_session` (Step 4 abaixo) muda — a lógica de ramificação por `billing_provider` não muda.

- [ ] **Step 2: Atualizar o helper `_settings_row` do arquivo de teste existente**

Em `apps/api/tests/unit/test_end_customer_billing_service.py`, a função `_settings_row` (linhas 26-36) não tem `billing_provider`/`stripe_account_id` — depois do Step 5 (produção passa a ler esses campos), todo teste existente que usa `_settings_row()` sem overrides quebraria com `AttributeError`. Atualizar o default:

```python
def _settings_row(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        enabled=True,
        billing_provider="standalone",
        stripe_account_id=None,
        stripe_secret_key_encrypted="cifrado",
        stripe_webhook_secret_encrypted="cifrado-webhook",
        end_customer_tokens_per_credit=500,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row
```

- [ ] **Step 3: Escrever o teste que falha**

Adicionar a `apps/api/tests/unit/test_end_customer_billing_service.py`, dentro de `class TestCreateEndCustomerCheckoutSession` (mesmo padrão dos testes já existentes nessa classe — `session.scalar = AsyncMock(side_effect=[...])`, mock de `service.stripe.checkout.Session.create` via `MagicMock`, leitura de `created.call_args.kwargs`):

```python
    async def test_checkout_connect_usa_direct_charge_na_conta_do_tenant(
        self, session, monkeypatch
    ) -> None:
        session.scalar = AsyncMock(
            side_effect=[
                _settings_row(billing_provider="connect", stripe_account_id="acct_123"),
                _package(),
            ]
        )
        created = MagicMock(
            return_value=SimpleNamespace(url="https://checkout.stripe.com/pay/cs_connect")
        )
        monkeypatch.setattr(service.stripe.checkout.Session, "create", created)

        url = await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

        assert url == "https://checkout.stripe.com/pay/cs_connect"
        kwargs = created.call_args.kwargs
        assert kwargs["stripe_account"] == "acct_123"
        assert kwargs["api_key"] == service.settings.stripe_connect_secret_key
        assert "application_fee_amount" not in kwargs

    async def test_checkout_connect_sem_stripe_account_id_levanta_erro(self, session) -> None:
        session.scalar = AsyncMock(
            return_value=_settings_row(billing_provider="connect", stripe_account_id=None)
        )

        with pytest.raises(BillingNotConfiguredError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)
```

- [ ] **Step 4: Rodar o teste e confirmar que falha**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py -v -k connect`
Expected: `test_checkout_connect_usa_direct_charge_na_conta_do_tenant` falha (hoje a função sempre usa a secret key cifrada do tenant, nunca `stripe_account=`/`stripe_connect_secret_key`; `kwargs` não tem a chave `stripe_account` → `KeyError`); `test_checkout_connect_sem_stripe_account_id_levanta_erro` também falha (hoje não existe essa checagem — a função segue até tentar descriptografar `stripe_secret_key_encrypted=None`, levantando um erro diferente do esperado).

- [ ] **Step 5: Implementar a ramificação**

Em `apps/api/app/services/end_customer_billing.py`, modificar `create_end_customer_checkout_session` (linhas 50-105):

```python
async def create_end_customer_checkout_session(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    contact_phone_number: str,
    package_id: uuid.UUID,
) -> str:
    billing_settings = await session.scalar(
        select(TenantBillingSettings).where(TenantBillingSettings.tenant_id == tenant_id)
    )
    if billing_settings is None or not billing_settings.enabled:
        raise BillingNotConfiguredError("Cobrança do cliente final não configurada pelo tenant")
    if (
        billing_settings.billing_provider == "standalone"
        and billing_settings.stripe_secret_key_encrypted is None
    ):
        raise BillingNotConfiguredError("Cobrança do cliente final não configurada pelo tenant")
    if billing_settings.billing_provider == "connect" and billing_settings.stripe_account_id is None:
        raise BillingNotConfiguredError("Cobrança do cliente final não configurada pelo tenant")

    package = await session.scalar(
        select(EndCustomerCreditPackage).where(
            EndCustomerCreditPackage.id == package_id,
            EndCustomerCreditPackage.tenant_id == tenant_id,
        )
    )
    if package is None or not package.active:
        raise InvalidPackageError("Pacote de créditos inválido")

    line_items = [
        {
            "price_data": {
                "currency": "brl",
                "unit_amount": int(package.price_brl * 100),
                "product_data": {"name": package.name},
            },
            "quantity": 1,
        }
    ]
    metadata = {
        "tenant_id": str(tenant_id),
        "contact_phone_number": contact_phone_number,
        "package_id": str(package_id),
        "kind": "end_customer_purchase",
    }

    try:
        if billing_settings.billing_provider == "connect":
            checkout_session = await asyncio.to_thread(
                stripe.checkout.Session.create,
                api_key=settings.stripe_connect_secret_key,
                stripe_account=billing_settings.stripe_account_id,
                mode="payment",
                line_items=line_items,
                metadata=metadata,
                success_url=f"{settings.web_app_url}/pagamento-confirmado",
                cancel_url=f"{settings.web_app_url}/pagamento-confirmado",
            )
        else:
            secret_key = decrypt_tenant_secret(billing_settings.stripe_secret_key_encrypted)
            checkout_session = await asyncio.to_thread(
                stripe.checkout.Session.create,
                api_key=secret_key,
                mode="payment",
                line_items=line_items,
                metadata=metadata,
                success_url=f"{settings.web_app_url}/pagamento-confirmado",
                cancel_url=f"{settings.web_app_url}/pagamento-confirmado",
            )
    except stripe.error.StripeError as exc:
        logger.error("Falha ao criar checkout do cliente final | erro=%s", exc)
        raise StripeApiError("Falha ao iniciar o pagamento — tente novamente em instantes") from exc

    return checkout_session.url
```

Adicionar `from app.core.config import settings` ao topo do arquivo se ainda não importado (conferir — `settings.web_app_url` já é usado, então provavelmente já está lá).

- [ ] **Step 6: Rodar o teste e confirmar que passa**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py -v`
Expected: todos passam — os 2 testes novos e todos os pré-existentes do caminho `standalone` (que continuam passando sem alteração, já que o Step 2 deu a `_settings_row` um default explícito de `billing_provider="standalone"`).

- [ ] **Step 7: Commit**

```bash
cd apps/api
git add app/services/end_customer_billing.py tests/unit/test_end_customer_billing_service.py
git commit -m "feat(api): checkout do cliente final ramifica por billing_provider (Direct charge no Connect)"
```

---

## Task 6: Frontend — onboarding embutido via Connect.js

**Files:**
- Modify: `apps/web/package.json`
- Create: `apps/web/src/components/ConnectAccountOnboarding.tsx`
- Modify: `apps/web/src/components/EndCustomerBillingPanel.tsx`
- Test: `apps/web/__tests__/EndCustomerBillingPanel.test.tsx`
- Test: `apps/web/__tests__/ConnectAccountOnboarding.test.tsx`

**Interfaces:**
- Consumes: `POST end-customer-billing/connect-account` (Task 3, via `backendFetch`), `GET end-customer-billing/settings` (Task 1 — campos novos `billing_provider`/`stripe_account_status`).
- Produces: `ConnectAccountOnboarding` — componente que renderiza o onboarding embutido; `EndCustomerBillingPanel` passa a mostrar esse componente (tenant `billing_provider="connect"` ou sem nenhuma linha configurada ainda) ou o formulário antigo de secret key (só quando `billing_provider="standalone"`).

- [ ] **Step 1: Adicionar a dependência**

Run: `cd apps/web && pnpm add @stripe/connect-js`
Expected: `package.json` ganha `"@stripe/connect-js": "^<versão instalada>"` em `dependencies`.

- [ ] **Step 2: Escrever o teste que falha — `ConnectAccountOnboarding`**

Criar `apps/web/__tests__/ConnectAccountOnboarding.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectAccountOnboarding } from "@/components/ConnectAccountOnboarding";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

vi.mock("@stripe/connect-js", () => ({
  loadConnectAndInitialize: vi.fn().mockResolvedValue({
    create: vi.fn().mockReturnValue(document.createElement("div")),
  }),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("ConnectAccountOnboarding", () => {
  it("busca o client_secret e monta o componente de onboarding embutido", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ client_secret: "secret_abc" }),
    });

    render(<ConnectAccountOnboarding />);

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith(
        "end-customer-billing/connect-account",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("mostra erro quando a criação da sessão falha", async () => {
    mockedFetch.mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "Falha ao iniciar a configuração de pagamentos" }),
    });

    render(<ConnectAccountOnboarding />);

    await waitFor(() =>
      expect(screen.getByText(/falha ao iniciar a configuração/i)).toBeInTheDocument(),
    );
  });
});
```

- [ ] **Step 3: Rodar o teste e confirmar que falha**

Run: `cd apps/web && pnpm vitest run __tests__/ConnectAccountOnboarding.test.tsx`
Expected: falha — módulo `@/components/ConnectAccountOnboarding` não existe.

- [ ] **Step 4: Implementar `ConnectAccountOnboarding`**

Criar `apps/web/src/components/ConnectAccountOnboarding.tsx`:

```tsx
"use client";

import { loadConnectAndInitialize } from "@stripe/connect-js";
import { useEffect, useRef, useState } from "react";

import { backendFetch } from "@/lib/client-api";

function extractErrorDetail(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export function ConnectAccountOnboarding() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function fetchClientSecret(): Promise<string> {
      const response = await backendFetch("end-customer-billing/connect-account", {
        method: "POST",
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(
          extractErrorDetail(body, "Falha ao iniciar a configuração de pagamentos."),
        );
      }
      return body.client_secret as string;
    }

    async function mount() {
      try {
        const connectInstance = await loadConnectAndInitialize({
          publishableKey: process.env.NEXT_PUBLIC_STRIPE_CONNECT_PUBLISHABLE_KEY ?? "",
          fetchClientSecret,
        });
        if (!active || !containerRef.current) return;
        const onboarding = connectInstance.create("account-onboarding");
        containerRef.current.appendChild(onboarding);
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Falha de conexão — tente novamente.");
      }
    }

    void mount();
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="max-w-xl">
      {error && (
        <p role="alert" className="mb-4 text-sm text-danger">
          {error}
        </p>
      )}
      <div ref={containerRef} />
    </div>
  );
}
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `cd apps/web && pnpm vitest run __tests__/ConnectAccountOnboarding.test.tsx`
Expected: 2 passed.

- [ ] **Step 6: Escrever o teste que falha — `EndCustomerBillingPanel` ramifica por `billing_provider`**

Em `apps/web/__tests__/EndCustomerBillingPanel.test.tsx`, adicionar `vi.mock("@/components/ConnectAccountOnboarding", ...)` no topo (junto do mock de `client-api`):

```tsx
vi.mock("@/components/ConnectAccountOnboarding", () => ({
  ConnectAccountOnboarding: () => <div>onboarding-connect-mock</div>,
}));
```

E o teste novo:

```tsx
it("mostra o onboarding Connect (não o formulário de secret key) quando billing_provider é connect", async () => {
  mockLoad({
    enabled: false,
    billing_mode: "credits",
    billing_provider: "connect",
    stripe_account_id: "acct_123",
    stripe_account_status: "onboarding",
    stripe_secret_key_configured: false,
    stripe_webhook_secret_configured: false,
    end_customer_tokens_per_credit: null,
  });

  render(<EndCustomerBillingPanel />);

  await waitFor(() => expect(screen.getByText("onboarding-connect-mock")).toBeInTheDocument());
  expect(screen.queryByLabelText(/secret key/i)).not.toBeInTheDocument();
});

it("mostra o formulário antigo de secret key quando billing_provider é standalone", async () => {
  mockLoad({
    enabled: false,
    billing_mode: "credits",
    billing_provider: "standalone",
    stripe_account_id: null,
    stripe_account_status: null,
    stripe_secret_key_configured: false,
    stripe_webhook_secret_configured: false,
    end_customer_tokens_per_credit: null,
  });

  render(<EndCustomerBillingPanel />);

  await waitFor(() => expect(screen.getByLabelText(/secret key/i)).toBeInTheDocument());
  expect(screen.queryByText("onboarding-connect-mock")).not.toBeInTheDocument();
});
```

Também é necessário adicionar `billing_provider: "standalone"`, `stripe_account_id: null`, `stripe_account_status: null` a `EMPTY_SETTINGS` (linha 20-28 do componente atual) e a todos os objetos de mock dos testes pré-existentes desse arquivo (as chamadas `mockLoad({...})` sem esses campos) — sem isso os testes antigos, que esperam ver o formulário de secret key, quebram, já que o tipo `Settings` agora tem esses campos obrigatórios e o componente vai decidir qual UI mostrar com base neles.

- [ ] **Step 7: Rodar os testes e confirmar que falham**

Run: `cd apps/web && pnpm vitest run __tests__/EndCustomerBillingPanel.test.tsx`
Expected: os 2 testes novos falham (o componente ainda sempre mostra o formulário de secret key, nunca o onboarding Connect).

- [ ] **Step 8: Implementar a ramificação em `EndCustomerBillingPanel`**

Em `apps/web/src/components/EndCustomerBillingPanel.tsx`:

Adicionar ao topo:

```tsx
import { ConnectAccountOnboarding } from "@/components/ConnectAccountOnboarding";
```

Ampliar o tipo `Settings` (linhas 8-18):

```tsx
type Settings = {
  tenant_id: string;
  enabled: boolean;
  billing_mode: string;
  billing_provider: string;
  stripe_account_id: string | null;
  stripe_account_status: string | null;
  stripe_secret_key_configured: boolean;
  stripe_webhook_secret_configured: boolean;
  end_customer_tokens_per_credit: number | null;
  webhook_url: string;
};
```

Ampliar `EMPTY_SETTINGS` (linhas 20-28):

```tsx
const EMPTY_SETTINGS: Settings = {
  tenant_id: "",
  enabled: false,
  billing_mode: "credits",
  billing_provider: "standalone",
  stripe_account_id: null,
  stripe_account_status: null,
  stripe_secret_key_configured: false,
  stripe_webhook_secret_configured: false,
  end_customer_tokens_per_credit: null,
  webhook_url: "",
};
```

E, no corpo de `EndCustomerBillingPanel` (dentro do `return`, antes do bloco `<section>` com o "Como configurar" — linha 183), substituir o bloco de "Como configurar" + o `<form onSubmit={handleSubmit}>` de secret key/webhook secret (linhas 183-304) por uma ramificação:

```tsx
{settings.billing_provider === "connect" ? (
  <section className="mb-8 max-w-xl">
    <h2 className="font-display text-base font-semibold text-ink">
      Configuração de pagamentos
    </h2>
    <p className="mt-1 text-sm text-muted">
      Preencha os dados abaixo pra receber os pagamentos dos seus clientes direto na
      sua conta — sem sair desta tela.
    </p>
    <div className="mt-4">
      <ConnectAccountOnboarding />
    </div>
    <label className="mt-6 flex items-center gap-2 text-sm text-ink">
      <input
        type="checkbox"
        checked={enabled}
        onChange={(event) => setEnabled(event.target.checked)}
        disabled={settings.stripe_account_status !== "active"}
      />
      Cobrar meus clientes pelo uso dos agentes
    </label>
  </section>
) : (
  // ... bloco existente de "Como configurar" + <form onSubmit={handleSubmit}> de
  // secret key/webhook secret, sem nenhuma alteração de conteúdo.
)}
```

(O bloco `// ...` acima é só uma referência de leitura — na implementação real, mover o JSX existente das linhas 183-304 pra dentro do ramo `else` do ternário, sem reescrever nenhuma linha dele.)

- [ ] **Step 9: Rodar os testes e confirmar que passam**

Run: `cd apps/web && pnpm vitest run __tests__/EndCustomerBillingPanel.test.tsx __tests__/ConnectAccountOnboarding.test.tsx`
Expected: todos passam.

- [ ] **Step 10: Rodar a suíte completa do `apps/web` pra checar regressão**

Run: `cd apps/web && pnpm test && pnpm lint`
Expected: sem falhas novas.

- [ ] **Step 11: Commit**

```bash
cd apps/web
git add package.json pnpm-lock.yaml src/components/ConnectAccountOnboarding.tsx src/components/EndCustomerBillingPanel.tsx __tests__/EndCustomerBillingPanel.test.tsx __tests__/ConnectAccountOnboarding.test.tsx
git commit -m "feat(web): onboarding embutido de Stripe Connect em /configuracoes/cobranca-clientes"
```

---

## Task 7: Documentação (`CLAUDE.md`)

**Files:**
- Modify: `/home/falcao/development/advoxs/CLAUDE.md`

**Interfaces:**
- Consumes: nada de código — só reflete o que as Tasks 1-6 já implementaram.

- [ ] **Step 1: Atualizar a subseção "Cobrança do cliente final" em `CLAUDE.md`**

Localizar a subseção `### Cobrança do cliente final — ✅ implementada (segunda camada, independente do billing acima)` e:
1. Adicionar um parágrafo novo descrevendo o modelo Connect (`billing_provider`, onboarding embutido via Connect.js/Account Session, capabilities `card_payments`+`pix`, Direct charge sem `application_fee_amount`, webhook único `/webhooks/stripe/connect`) como a via atual pra tenants novos, e o modelo standalone (secret key colada) como legado/transitório pra tenants já configurados antes desta entrega.
2. Adicionar uma referência à spec: `docs/superpowers/specs/2026-07-24-stripe-connect-cobranca-cliente-final-design.md`.
3. Atualizar a lista de rotas do parágrafo introdutório do arquivo (topo do `CLAUDE.md`, onde lista `/api/v1/end-customer-billing/{settings,packages}`) pra incluir `/connect-account` e `/account-status`.
4. Adicionar um item à lista de "Pendências da cobrança do cliente final" (final da seção): "Depreciação do modelo `standalone` — pendente até todos os tenants reais migrarem pra Connect (ver spec)."

Escrever o texto real (sem placeholder) refletindo exatamente o que foi implementado nas Tasks 1-6 — usar a spec (`docs/superpowers/specs/2026-07-24-stripe-connect-cobranca-cliente-final-design.md`) como fonte, mas descrever o estado JÁ implementado (✅), não o desenho futuro.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: documenta a migração da cobrança do cliente final pra Stripe Connect"
```
