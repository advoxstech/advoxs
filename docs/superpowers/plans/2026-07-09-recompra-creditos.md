# Recompra de Créditos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Página `/creditos` para escritórios já cadastrados comprarem mais créditos, reaproveitando a integração com a Stripe já existente no cadastro self-service.

**Architecture:** O `api` ganha rotas autenticadas (`get_current_tenant`) que criam uma Stripe Checkout Session com metadata `flow="recompra"` + `tenant_id` (vindo do JWT, nunca do corpo da requisição); o webhook único (`POST /webhooks/stripe`) passa a ramificar por esse campo — `recompra` credita um tenant existente, ausência do campo (formato antigo, já em produção) continua criando tenant novo. O front ganha uma página com saldo + pacotes + polling pós-pagamento, mesmo padrão do `/cadastro/sucesso`.

**Tech Stack:** FastAPI + SQLAlchemy async + `stripe-python` (api), Next.js 15 App Router + React (web).

## Global Constraints

- **`tenant_id` nunca vem do corpo de uma requisição do cliente** — só do contexto autenticado (`get_current_tenant`) no momento de criar a sessão de checkout. É gravado na metadata pelo servidor e lido de volta no webhook (seguro porque só o `api` escreve essa metadata).
- **Sem histórico de transações, sem bloqueio por status do tenant, sem mudança na regra de saldo zerado** — fora de escopo desta entrega (spec).
- **Compatibilidade retroativa do webhook**: metadata sem o campo `flow` (formato já em produção, do cadastro self-service) precisa continuar funcionando exatamente como hoje — nenhuma mudança observável no fluxo de signup.
- **Pegadinha do SDK `stripe-python`**: `event["data"]["object"]` é um `StripeObject` real, não dict — usar `.to_dict()` antes de `.get()` (já corrigido no código existente; manter esse cuidado em qualquer código novo que toque nesse objeto).
- Mensagens/comentários em pt-BR com acentuação correta.
- Comandos: `apps/api` → `uv run pytest tests/unit`, `uv run ruff check .`, `uv run ruff format --check .`. `apps/web` → `pnpm test`, `pnpm lint`, `pnpm build` (via `npx --yes pnpm@9 <comando>` se `pnpm` não estiver disponível globalmente).

---

### Task 1: `api` — service layer (checkout de recompra + webhook ramificado)

**Files:**
- Modify: `apps/api/app/services/billing.py`
- Modify: `apps/api/tests/unit/test_billing_service.py`

**Interfaces:**
- Consumes: `CreditPackage`, `CreditTransaction`, `Tenant`, `User` (`app/models`, já existentes); `settings.web_app_url` (`app/core/config`, já existente).
- Produces: `create_recompra_checkout_session(session, tenant_id: uuid.UUID, credit_package_id: uuid.UUID) -> str` em `app.services.billing` (reaproveita `InvalidPackageError`/`StripeApiError` já existentes); `process_checkout_completed` passa a ramificar internamente por `metadata["flow"]`, sem mudar sua assinatura pública.

- [ ] **Step 1: Escrever os testes que falham**

Em `apps/api/tests/unit/test_billing_service.py`, adicionar o import de `create_recompra_checkout_session`:

```python
from app.services.billing import (
    EmailAlreadyExistsError,
    InvalidPackageError,
    StripeApiError,
    create_checkout_session,
    create_recompra_checkout_session,
    process_checkout_completed,
)
```

Adicionar, após a classe `TestCreateCheckoutSession` (antes de `class TestProcessCheckoutCompleted:`):

```python
class TestCreateRecompraCheckoutSession:
    async def test_pacote_inexistente_levanta_erro(self, session) -> None:
        session.get.return_value = None

        with pytest.raises(InvalidPackageError):
            await create_recompra_checkout_session(session, uuid.uuid4(), PACKAGE_ID)

    async def test_pacote_inativo_levanta_erro(self, session) -> None:
        session.get.return_value = _package(active=False)

        with pytest.raises(InvalidPackageError):
            await create_recompra_checkout_session(session, uuid.uuid4(), PACKAGE_ID)

    async def test_sucesso_cria_sessao_com_metadata_de_recompra(self, session, monkeypatch) -> None:
        session.get.return_value = _package()
        created = MagicMock(
            return_value=SimpleNamespace(url="https://checkout.stripe.com/pay/cs_456")
        )
        monkeypatch.setattr(billing.stripe.checkout.Session, "create", created)
        tenant_id = uuid.uuid4()

        url = await create_recompra_checkout_session(session, tenant_id, PACKAGE_ID)

        assert url == "https://checkout.stripe.com/pay/cs_456"
        kwargs = created.call_args.kwargs
        assert kwargs["mode"] == "payment"
        assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 25000
        assert kwargs["metadata"] == {
            "flow": "recompra",
            "tenant_id": str(tenant_id),
            "credit_package_id": str(PACKAGE_ID),
        }
        assert "/creditos" in kwargs["success_url"]
        assert "/creditos" in kwargs["cancel_url"]

    async def test_falha_na_stripe_levanta_stripe_api_error(self, session, monkeypatch) -> None:
        session.get.return_value = _package()

        def _raise(*args, **kwargs):
            raise billing.stripe.error.StripeError("falhou")

        monkeypatch.setattr(billing.stripe.checkout.Session, "create", _raise)

        with pytest.raises(StripeApiError):
            await create_recompra_checkout_session(session, uuid.uuid4(), PACKAGE_ID)
```

Adicionar, após a classe `TestProcessCheckoutCompleted` já existente (ao final do arquivo, mesma indentação de nível de módulo):

```python
class TestProcessCheckoutCompletedRecompra:
    def _recompra_session(self, **overrides) -> dict:
        metadata = {
            "flow": "recompra",
            "tenant_id": str(uuid.uuid4()),
            "credit_package_id": str(PACKAGE_ID),
        }
        metadata.update(overrides)
        return {"id": "cs_789", "metadata": metadata}

    async def test_credita_tenant_existente_sem_criar_user(self, session) -> None:
        session.scalar.return_value = None
        tenant = SimpleNamespace(id=uuid.uuid4(), credit_balance=500)
        session.get = AsyncMock(side_effect=[_package(), tenant])
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        await process_checkout_completed(
            session, self._recompra_session(tenant_id=str(tenant.id))
        )

        assert tenant.credit_balance == 500 + 2750
        assert len(added) == 1
        transaction = added[0]
        assert transaction.tenant_id == tenant.id
        assert transaction.type == "purchase"
        assert transaction.amount_credits == 2750
        assert transaction.stripe_payment_id == "cs_789"
        session.commit.assert_awaited_once()

    async def test_stripe_session_real_funciona_na_recompra(self, session) -> None:
        """Regressão: a mesma pegadinha do StripeObject sem .get() se aplica
        à recompra — cobrir explicitamente pra não regredir."""
        session.scalar.return_value = None
        tenant = SimpleNamespace(id=uuid.uuid4(), credit_balance=0)
        session.get = AsyncMock(side_effect=[_package(), tenant])
        session.add = MagicMock()

        raw = self._recompra_session(tenant_id=str(tenant.id))
        real_session = stripe.StripeObject.construct_from(raw, "sk_test_fake")

        await process_checkout_completed(session, real_session)

        assert tenant.credit_balance == 2750
        session.commit.assert_awaited_once()

    async def test_tenant_inexistente_nao_processa(self, session) -> None:
        session.scalar.return_value = None
        session.get = AsyncMock(side_effect=[_package(), None])
        session.add = MagicMock()

        await process_checkout_completed(session, self._recompra_session())

        session.add.assert_not_called()

    async def test_pacote_inexistente_na_recompra_nao_processa(self, session) -> None:
        session.scalar.return_value = None
        session.get = AsyncMock(return_value=None)
        session.add = MagicMock()

        await process_checkout_completed(session, self._recompra_session())

        session.add.assert_not_called()

    async def test_metadata_incompleta_na_recompra_nao_processa(self, session) -> None:
        session.scalar.return_value = None
        session.add = MagicMock()

        await process_checkout_completed(
            session, {"id": "cs_789", "metadata": {"flow": "recompra"}}
        )

        session.add.assert_not_called()

    async def test_integrity_error_na_recompra_e_tratado(self, session) -> None:
        session.scalar.return_value = None
        tenant = SimpleNamespace(id=uuid.uuid4(), credit_balance=0)
        session.get = AsyncMock(side_effect=[_package(), tenant])
        session.add = MagicMock()
        session.commit = AsyncMock(
            side_effect=billing.IntegrityError("stmt", {}, Exception("dup"))
        )
        session.rollback = AsyncMock()

        await process_checkout_completed(session, self._recompra_session())

        session.rollback.assert_awaited_once()

    async def test_signup_sem_flow_continua_funcionando(self, session) -> None:
        """Regressão: metadata sem 'flow' (formato antigo, já em produção)
        continua indo pro fluxo de cadastro — nenhuma mudança observável."""
        session.scalar.return_value = None
        session.get.return_value = _package()
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

        session.flush = AsyncMock(side_effect=fake_flush)

        metadata = {
            "tenant_name": "Escritório Teste",
            "email": "a@b.com",
            "password_hash": "hash-fake",
            "credit_package_id": str(PACKAGE_ID),
        }
        await process_checkout_completed(session, {"id": "cs_999", "metadata": metadata})

        assert len(added) == 3
        session.commit.assert_awaited_once()
```

- [ ] **Step 2: Rodar os testes e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_billing_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_recompra_checkout_session'`.

- [ ] **Step 3: `create_recompra_checkout_session`**

Em `apps/api/app/services/billing.py`, adicionar (após `create_checkout_session`, antes de `process_checkout_completed`):

```python
async def create_recompra_checkout_session(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    credit_package_id: uuid.UUID,
) -> str:
    """Checkout de recompra — tenant já existe e está autenticado; o
    tenant_id vem sempre do contexto autenticado (nunca do corpo da
    requisição do cliente) e é gravado na metadata pelo servidor."""
    package = await session.get(CreditPackage, credit_package_id)
    if package is None or not package.active:
        raise InvalidPackageError("Pacote de créditos inválido")

    try:
        checkout_session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": "brl",
                        "unit_amount": int(package.price_brl * 100),
                        "product_data": {"name": f"Advoxs — {package.name}"},
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "flow": "recompra",
                "tenant_id": str(tenant_id),
                "credit_package_id": str(credit_package_id),
            },
            success_url=f"{settings.web_app_url}/creditos?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{settings.web_app_url}/creditos",
        )
    except stripe.error.StripeError as exc:
        logger.error("Falha ao criar sessão de recompra | erro=%s", exc)
        raise StripeApiError("Falha ao iniciar o pagamento — tente novamente em instantes") from exc

    return checkout_session.url
```

- [ ] **Step 4: Ramificar `process_checkout_completed` por `flow`**

Substituir a função `process_checkout_completed` inteira (do `async def process_checkout_completed` até o final do arquivo) por:

```python
async def process_checkout_completed(session: AsyncSession, stripe_session: dict) -> None:
    """Credita a compra (cadastro novo ou recompra de tenant existente) a
    partir da metadata da sessão paga.

    Idempotente: uma sessão já processada (mesmo id) não cria duplicata.
    """
    session_id = stripe_session["id"]
    already_processed = await session.scalar(
        select(CreditTransaction.id).where(CreditTransaction.stripe_payment_id == session_id)
    )
    if already_processed is not None:
        logger.info("Webhook duplicado, ignorando | session=%s", session_id)
        return

    # stripe_session é um StripeObject real (não um dict): não implementa
    # .get(), só acesso via []/in — to_dict() normaliza pra dict puro.
    raw_metadata = stripe_session["metadata"] if "metadata" in stripe_session else {}
    metadata = raw_metadata.to_dict() if hasattr(raw_metadata, "to_dict") else dict(raw_metadata)

    if metadata.get("flow") == "recompra":
        await _process_recompra(session, session_id, metadata)
        return

    await _process_signup(session, session_id, metadata)


async def _process_signup(session: AsyncSession, session_id: str, metadata: dict) -> None:
    tenant_name = metadata.get("tenant_name")
    email = metadata.get("email")
    password_hash = metadata.get("password_hash")
    credit_package_id = metadata.get("credit_package_id")
    if not all([tenant_name, email, password_hash, credit_package_id]):
        logger.error("Metadata incompleta no checkout.session.completed | session=%s", session_id)
        return

    try:
        package_id = uuid.UUID(credit_package_id)
    except ValueError:
        logger.error(
            "credit_package_id malformado no checkout.session.completed | session=%s", session_id
        )
        return

    package = await session.get(CreditPackage, package_id)
    if package is None:
        logger.error("Pacote não encontrado ao processar pagamento | session=%s", session_id)
        return

    tenant = Tenant(name=tenant_name, email_contato=email, credit_balance=package.credits_granted)
    session.add(tenant)
    await session.flush()

    user = User(
        tenant_id=tenant.id,
        name=tenant_name,
        email=email,
        password_hash=password_hash,
        role="admin",
    )
    session.add(user)

    session.add(
        CreditTransaction(
            tenant_id=tenant.id,
            type="purchase",
            amount_credits=package.credits_granted,
            credit_package_id=package.id,
            stripe_payment_id=session_id,
            description=f"Compra do pacote {package.name}",
        )
    )

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.critical(
            "Pagamento aprovado mas não foi possível provisionar o tenant "
            "(e-mail já existe?) | session=%s email=%s",
            session_id,
            email,
        )


async def _process_recompra(session: AsyncSession, session_id: str, metadata: dict) -> None:
    tenant_id_raw = metadata.get("tenant_id")
    credit_package_id = metadata.get("credit_package_id")
    if not all([tenant_id_raw, credit_package_id]):
        logger.error("Metadata incompleta na recompra | session=%s", session_id)
        return

    try:
        tenant_id = uuid.UUID(tenant_id_raw)
        package_id = uuid.UUID(credit_package_id)
    except ValueError:
        logger.error(
            "tenant_id/credit_package_id malformado na recompra | session=%s", session_id
        )
        return

    package = await session.get(CreditPackage, package_id)
    if package is None:
        logger.error("Pacote não encontrado ao processar recompra | session=%s", session_id)
        return

    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        logger.error("Tenant não encontrado ao processar recompra | session=%s", session_id)
        return

    tenant.credit_balance += package.credits_granted
    session.add(
        CreditTransaction(
            tenant_id=tenant.id,
            type="purchase",
            amount_credits=package.credits_granted,
            credit_package_id=package.id,
            stripe_payment_id=session_id,
            description=f"Compra do pacote {package.name}",
        )
    )

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.critical(
            "Pagamento de recompra aprovado mas não foi possível gravar a "
            "transação | session=%s tenant_id=%s",
            session_id,
            tenant_id,
        )
```

- [ ] **Step 5: Rodar os testes e ver passar**

Run: `cd apps/api && uv run pytest tests/unit/test_billing_service.py -v`
Expected: PASS em todos (os já existentes + os novos desta task).

- [ ] **Step 6: Rodar a suíte completa e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos PASS, ruff limpo.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/billing.py apps/api/tests/unit/test_billing_service.py
git commit -m "feat(api): checkout de recompra de créditos e webhook ramificado por flow"
```

---

### Task 2: `api` — schemas e rotas de billing

**Files:**
- Create: `apps/api/app/schemas/billing.py`
- Create: `apps/api/app/api/v1/billing.py`
- Modify: `apps/api/app/api/v1/router.py`
- Create: `apps/api/tests/unit/test_billing_routes.py`

**Interfaces:**
- Consumes: `TenantContext`/`get_current_tenant` (`app/api/deps.py`, já existente); `create_recompra_checkout_session`/`InvalidPackageError`/`StripeApiError` (Task 1); `Tenant`/`CreditTransaction` (`app/models`, já existentes).
- Produces: `GET /api/v1/billing/balance`, `POST /api/v1/billing/checkout`, `GET /api/v1/billing/status`.

- [ ] **Step 1: Schemas**

Criar `apps/api/app/schemas/billing.py`:

```python
import uuid

from pydantic import BaseModel


class BillingBalanceOut(BaseModel):
    credit_balance: int


class BillingCheckoutRequest(BaseModel):
    credit_package_id: uuid.UUID


class BillingCheckoutUrlOut(BaseModel):
    checkout_url: str


class BillingStatusOut(BaseModel):
    ready: bool
```

- [ ] **Step 2: Escrever os testes que falham**

Criar `apps/api/tests/unit/test_billing_routes.py`:

```python
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.billing as billing_module
from app.api.deps import TenantContext, get_current_tenant
from app.core.db import get_session
from app.main import app
from app.services.billing import InvalidPackageError, StripeApiError

TENANT_ID = uuid.uuid4()
PACKAGE_ID = uuid.uuid4()


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture
def client(session):
    async def override_tenant():
        return TenantContext(user_id=uuid.uuid4(), tenant_id=TENANT_ID, role="admin")

    async def override_session():
        yield session

    app.dependency_overrides[get_current_tenant] = override_tenant
    app.dependency_overrides[get_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestBalance:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).get("/api/v1/billing/balance")
        assert response.status_code == 401

    def test_retorna_saldo_do_tenant_autenticado(self, client, session) -> None:
        session.get.return_value = MagicMock(credit_balance=1500)

        response = client.get("/api/v1/billing/balance")

        assert response.status_code == 200
        assert response.json() == {"credit_balance": 1500}


class TestCheckout:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).post(
            "/api/v1/billing/checkout", json={"credit_package_id": str(PACKAGE_ID)}
        )
        assert response.status_code == 401

    def test_sucesso_retorna_checkout_url(self, client, monkeypatch) -> None:
        create = AsyncMock(return_value="https://checkout.stripe.com/pay/cs_456")
        monkeypatch.setattr(billing_module, "create_recompra_checkout_session", create)

        response = client.post(
            "/api/v1/billing/checkout", json={"credit_package_id": str(PACKAGE_ID)}
        )

        assert response.status_code == 200
        assert response.json()["checkout_url"] == "https://checkout.stripe.com/pay/cs_456"
        args = create.call_args.args
        assert args[1] == TENANT_ID
        assert args[2] == PACKAGE_ID

    def test_pacote_invalido_retorna_400(self, client, monkeypatch) -> None:
        create = AsyncMock(side_effect=InvalidPackageError("pacote inválido"))
        monkeypatch.setattr(billing_module, "create_recompra_checkout_session", create)

        response = client.post(
            "/api/v1/billing/checkout", json={"credit_package_id": str(PACKAGE_ID)}
        )

        assert response.status_code == 400

    def test_falha_stripe_retorna_502(self, client, monkeypatch) -> None:
        create = AsyncMock(side_effect=StripeApiError("falhou"))
        monkeypatch.setattr(billing_module, "create_recompra_checkout_session", create)

        response = client.post(
            "/api/v1/billing/checkout", json={"credit_package_id": str(PACKAGE_ID)}
        )

        assert response.status_code == 502


class TestStatus:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).get(
            "/api/v1/billing/status", params={"session_id": "cs_123"}
        )
        assert response.status_code == 401

    def test_ready_quando_transacao_existe(self, client, session) -> None:
        session.scalar.return_value = uuid.uuid4()

        response = client.get("/api/v1/billing/status", params={"session_id": "cs_123"})

        assert response.status_code == 200
        assert response.json() == {"ready": True}

    def test_not_ready_quando_transacao_nao_existe(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.get("/api/v1/billing/status", params={"session_id": "cs_123"})

        assert response.json() == {"ready": False}
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_billing_routes.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'app.api.v1.billing'`.

- [ ] **Step 4: Rotas**

Criar `apps/api/app/api/v1/billing.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant
from app.core.db import get_session
from app.models import CreditTransaction, Tenant
from app.schemas.billing import (
    BillingBalanceOut,
    BillingCheckoutRequest,
    BillingCheckoutUrlOut,
    BillingStatusOut,
)
from app.services.billing import (
    InvalidPackageError,
    StripeApiError,
    create_recompra_checkout_session,
)

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/balance")
async def get_balance(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> BillingBalanceOut:
    tenant = await session.get(Tenant, ctx.tenant_id)
    return BillingBalanceOut(credit_balance=tenant.credit_balance)


@router.post("/checkout")
async def checkout(
    body: BillingCheckoutRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> BillingCheckoutUrlOut:
    try:
        checkout_url = await create_recompra_checkout_session(
            session, ctx.tenant_id, body.credit_package_id
        )
    except InvalidPackageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except StripeApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return BillingCheckoutUrlOut(checkout_url=checkout_url)


@router.get("/status")
async def billing_status(
    session_id: str = Query(...),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> BillingStatusOut:
    found = await session.scalar(
        select(CreditTransaction.id).where(CreditTransaction.stripe_payment_id == session_id)
    )
    return BillingStatusOut(ready=found is not None)
```

- [ ] **Step 5: Registrar no router principal**

Em `apps/api/app/api/v1/router.py`, adicionar:

```python
from app.api.v1.billing import router as billing_router
```

```python
api_router.include_router(billing_router)
```

- [ ] **Step 6: Rodar todos os testes e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos PASS, ruff limpo.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/schemas/billing.py apps/api/app/api/v1/billing.py apps/api/app/api/v1/router.py apps/api/tests/unit/test_billing_routes.py
git commit -m "feat(api): rotas de recompra de créditos (balance, checkout, status)"
```

---

### Task 3: `web` — extrair `TenantNav` compartilhado

**Files:**
- Create: `apps/web/src/components/TenantNav.tsx`
- Modify: `apps/web/src/app/conversas/page.tsx`
- Modify: `apps/web/src/app/base-de-conhecimento/page.tsx`
- Modify: `apps/web/src/app/configuracoes/whatsapp/page.tsx`
- Test: `apps/web/__tests__/TenantNav.test.tsx`

**Interfaces:**
- Consumes: `logout` (`@/app/conversas/actions`, já existente).
- Produces: `TenantNav({ active }: { active: "conversas" | "base" | "config" | "creditos" | null })` em `@/components/TenantNav`.

**Contexto**: as 3 páginas do tenant hoje duplicam o mesmo bloco de `<nav>` manualmente (mesmo padrão do painel de admin antes do `AdminNav`). Como a Task 4 vai adicionar um 4º item ("Créditos"), esta task extrai o bloco pra um componente único primeiro. Nenhuma mudança visual: as 3 páginas existentes continuam com o mesmo item marcado como ativo que já tinham.

- [ ] **Step 1: Escrever o teste que falha**

Criar `apps/web/__tests__/TenantNav.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TenantNav } from "@/components/TenantNav";

describe("TenantNav", () => {
  it("renderiza o item ativo como texto (não link) e os demais como links", () => {
    render(<TenantNav active="conversas" />);

    expect(screen.getByText("Conversas").closest("a")).toBeNull();
    expect(screen.getByText("Base").closest("a")).toHaveAttribute(
      "href",
      "/base-de-conhecimento",
    );
    expect(screen.getByText("Config").closest("a")).toHaveAttribute(
      "href",
      "/configuracoes/whatsapp",
    );
    expect(screen.getByText("Créditos").closest("a")).toHaveAttribute("href", "/creditos");
  });

  it("marca creditos como ativo quando active='creditos'", () => {
    render(<TenantNav active="creditos" />);

    expect(screen.getByText("Créditos").closest("a")).toBeNull();
    expect(screen.getByText("Conversas").closest("a")).toHaveAttribute("href", "/conversas");
  });

  it("renderiza todos os itens como links quando active=null", () => {
    render(<TenantNav active={null} />);

    expect(screen.getByText("Conversas").closest("a")).not.toBeNull();
    expect(screen.getByText("Base").closest("a")).not.toBeNull();
    expect(screen.getByText("Config").closest("a")).not.toBeNull();
    expect(screen.getByText("Créditos").closest("a")).not.toBeNull();
  });

  it("renderiza o botão Sair", () => {
    render(<TenantNav active="conversas" />);

    expect(screen.getByRole("button", { name: "Sair" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/web && npx --yes pnpm@9 test -- TenantNav`
Expected: FAIL — `@/components/TenantNav` não existe.

- [ ] **Step 3: Criar `TenantNav`**

Criar `apps/web/src/components/TenantNav.tsx`:

```tsx
import Link from "next/link";

import { logout } from "@/app/conversas/actions";

type TenantNavItem = "conversas" | "base" | "config" | "creditos";

const ITEMS: { key: TenantNavItem; href: string; label: string }[] = [
  { key: "conversas", href: "/conversas", label: "Conversas" },
  { key: "base", href: "/base-de-conhecimento", label: "Base" },
  { key: "config", href: "/configuracoes/whatsapp", label: "Config" },
  { key: "creditos", href: "/creditos", label: "Créditos" },
];

export function TenantNav({ active }: { active: TenantNavItem | null }) {
  return (
    <nav className="flex w-14 shrink-0 flex-col items-center justify-between bg-ink py-5">
      <div className="flex flex-col items-center gap-6">
        <span className="font-display text-2xl font-semibold text-ground" aria-label="Advoxs">
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
      <form action={logout}>
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

Run: `cd apps/web && npx --yes pnpm@9 test -- TenantNav`
Expected: PASS (4/4).

- [ ] **Step 5: Usar `TenantNav` nas 3 páginas existentes**

Substituir `apps/web/src/app/conversas/page.tsx` por:

```tsx
import { ConversationsPanel } from "@/components/ConversationsPanel";
import { TenantNav } from "@/components/TenantNav";

export default function ConversasPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="conversas" />
      <ConversationsPanel />
    </div>
  );
}
```

Substituir `apps/web/src/app/base-de-conhecimento/page.tsx` por:

```tsx
import { KnowledgeBasePanel } from "@/components/KnowledgeBasePanel";
import { TenantNav } from "@/components/TenantNav";

export default function BaseDeConhecimentoPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="base" />
      <KnowledgeBasePanel />
    </div>
  );
}
```

Substituir `apps/web/src/app/configuracoes/whatsapp/page.tsx` por:

```tsx
import { TenantNav } from "@/components/TenantNav";
import { WhatsAppConnectionPanel } from "@/components/WhatsAppConnectionPanel";

export default function ConfiguracoesWhatsAppPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="config" />
      <WhatsAppConnectionPanel />
    </div>
  );
}
```

- [ ] **Step 6: Rodar toda a suíte, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde — as 3 páginas continuam renderizando (nenhum teste existente cobre o HTML da nav diretamente, só os componentes de conteúdo).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/components/TenantNav.tsx apps/web/src/app/conversas/page.tsx apps/web/src/app/base-de-conhecimento/page.tsx apps/web/src/app/configuracoes/whatsapp/page.tsx apps/web/__tests__/TenantNav.test.tsx
git commit -m "refactor(web): extrair TenantNav compartilhado entre as páginas do escritório"
```

---

### Task 4: `web` — página e painel de recompra (`/creditos`)

**Files:**
- Modify: `apps/web/src/lib/backend.ts`
- Create: `apps/web/src/components/CreditosPanel.tsx`
- Create: `apps/web/src/app/creditos/page.tsx`
- Test: `apps/web/__tests__/CreditosPanel.test.tsx`

**Interfaces:**
- Consumes: `backendFetch` (`@/lib/client-api`, já existente); `TenantNav` (Task 3); `CreditPackage` (`@/lib/types`, já existente); `GET billing/balance`, `POST billing/checkout`, `GET billing/status` (Task 2).

- [ ] **Step 1: Allowlist do proxy**

Em `apps/web/src/lib/backend.ts`, trocar:

```ts
const ALLOWED_PREFIXES = ["conversations", "knowledge-base", "whatsapp", "signup"];
```

por:

```ts
const ALLOWED_PREFIXES = ["conversations", "knowledge-base", "whatsapp", "signup", "billing"];
```

- [ ] **Step 2: Escrever o teste que falha**

Criar `apps/web/__tests__/CreditosPanel.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CreditosPanel } from "@/components/CreditosPanel";
import { backendFetch } from "@/lib/client-api";
import type { CreditPackage } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const PACKAGES: CreditPackage[] = [
  { id: "p1", name: "Starter", price_brl: 100, credits_granted: 1000 },
  { id: "p2", name: "Growth", price_brl: 250, credits_granted: 2750 },
];

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("CreditosPanel", () => {
  it("carrega e exibe o saldo atual", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ credit_balance: 1500 }) });

    render(<CreditosPanel packages={PACKAGES} sessionId={null} />);

    await waitFor(() => expect(screen.getByText("1500 créditos")).toBeInTheDocument());
  });

  it("renderiza os pacotes recebidos por prop", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ credit_balance: 0 }) });

    render(<CreditosPanel packages={PACKAGES} sessionId={null} />);

    expect(screen.getByText("Starter")).toBeInTheDocument();
    expect(screen.getByText("Growth")).toBeInTheDocument();
  });

  it("clicar em Comprar chama o checkout com o pacote certo", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "billing/balance") {
        return { ok: true, json: async () => ({ credit_balance: 0 }) };
      }
      if (path === "billing/checkout") {
        expect(JSON.parse(init?.body as string)).toEqual({ credit_package_id: "p2" });
        return { ok: true, json: async () => ({ checkout_url: "https://checkout.stripe.com/x" }) };
      }
      throw new Error(`chamada inesperada: ${path}`);
    });

    render(<CreditosPanel packages={PACKAGES} sessionId={null} />);
    await waitFor(() => expect(screen.getByText("Growth")).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole("button", { name: "Comprar" })[1]);

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith(
        "billing/checkout",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("mostra 'Confirmando' enquanto o pagamento não é confirmado, com sessionId", async () => {
    mockedFetch.mockImplementation(async (path: string) => {
      if (path === "billing/balance") {
        return { ok: true, json: async () => ({ credit_balance: 0 }) };
      }
      if (path.startsWith("billing/status")) {
        return { ok: true, json: async () => ({ ready: false }) };
      }
      throw new Error(`chamada inesperada: ${path}`);
    });

    render(<CreditosPanel packages={PACKAGES} sessionId="cs_123" pollMs={0} />);

    await waitFor(() => expect(screen.getByText("Confirmando seu pagamento…")).toBeInTheDocument());
  });

  it("some com 'Confirmando' e atualiza o saldo quando o pagamento confirma", async () => {
    let statusReady = false;
    mockedFetch.mockImplementation(async (path: string) => {
      if (path === "billing/balance") {
        return {
          ok: true,
          json: async () => ({ credit_balance: statusReady ? 2750 : 0 }),
        };
      }
      if (path.startsWith("billing/status")) {
        statusReady = true;
        return { ok: true, json: async () => ({ ready: true }) };
      }
      throw new Error(`chamada inesperada: ${path}`);
    });

    render(<CreditosPanel packages={PACKAGES} sessionId="cs_123" pollMs={0} />);

    await waitFor(() =>
      expect(screen.queryByText("Confirmando seu pagamento…")).not.toBeInTheDocument(),
    );
    await waitFor(() => expect(screen.getByText("2750 créditos")).toBeInTheDocument());
  });
});
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `cd apps/web && npx --yes pnpm@9 test -- CreditosPanel`
Expected: FAIL — `@/components/CreditosPanel` não existe.

- [ ] **Step 4: Criar `CreditosPanel`**

Criar `apps/web/src/components/CreditosPanel.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type { CreditPackage } from "@/lib/types";

const MAX_ATTEMPTS = 8;

export function CreditosPanel({
  packages,
  sessionId,
  pollMs = 2000,
}: {
  packages: CreditPackage[];
  sessionId: string | null;
  pollMs?: number;
}) {
  const [balance, setBalance] = useState<number | null>(null);
  const [confirming, setConfirming] = useState(sessionId !== null);
  const [attempts, setAttempts] = useState(0);
  const [purchasingId, setPurchasingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function loadBalance() {
    const response = await backendFetch("billing/balance");
    if (response.ok) {
      const body = await response.json();
      setBalance(body.credit_balance);
    }
  }

  useEffect(() => {
    void loadBalance();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function checkStatus() {
    if (!sessionId) return;
    try {
      const response = await backendFetch(
        `billing/status?session_id=${encodeURIComponent(sessionId)}`,
      );
      if (response.ok) {
        const body = await response.json();
        if (body.ready) {
          setConfirming(false);
          await loadBalance();
          return;
        }
      }
    } catch {
      // rede instável durante o polling — só tenta de novo no próximo ciclo
    }
    setAttempts((prev) => prev + 1);
  }

  useEffect(() => {
    void checkStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId || !confirming || attempts >= MAX_ATTEMPTS) {
      if (attempts >= MAX_ATTEMPTS) setConfirming(false);
      return;
    }
    const interval = setInterval(() => void checkStatus(), pollMs);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, confirming, attempts, pollMs]);

  async function handleComprar(packageId: string) {
    setError(null);
    setPurchasingId(packageId);
    try {
      const response = await backendFetch("billing/checkout", {
        method: "POST",
        body: JSON.stringify({ credit_package_id: packageId }),
      });
      if (!response.ok) {
        setError("Não foi possível iniciar o pagamento. Tente novamente.");
        return;
      }
      const body = await response.json();
      window.location.href = body.checkout_url;
    } catch {
      setError("Não foi possível iniciar o pagamento. Tente novamente.");
    } finally {
      setPurchasingId(null);
    }
  }

  return (
    <div className="flex flex-col gap-8 p-8">
      <div>
        <p className="font-mono text-[11px] uppercase tracking-[0.15em] text-muted">
          Saldo atual
        </p>
        <p className="mt-1 font-display text-4xl font-semibold text-ink">
          {balance === null ? "…" : `${balance} créditos`}
        </p>
      </div>

      {confirming && (
        <p className="rounded-sm border border-line bg-surface px-4 py-3 text-sm text-muted">
          Confirmando seu pagamento…
        </p>
      )}

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="flex flex-col gap-3">
        {packages.map((pkg) => (
          <div
            key={pkg.id}
            className="flex items-center justify-between gap-4 rounded-sm border border-line bg-surface px-4 py-3 text-sm"
          >
            <span>
              <span className="font-medium text-ink">{pkg.name}</span>{" "}
              <span className="text-muted">— {pkg.credits_granted} créditos</span>
            </span>
            <div className="flex items-center gap-3">
              <span className="font-mono text-xs text-muted">
                R$ {Number(pkg.price_brl).toFixed(2)}
              </span>
              <button
                type="button"
                onClick={() => void handleComprar(pkg.id)}
                disabled={purchasingId !== null}
                className="rounded-sm bg-accent px-3 py-1.5 text-sm font-medium text-surface disabled:opacity-60"
              >
                {purchasingId === pkg.id ? "Redirecionando…" : "Comprar"}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Rodar e ver passar**

Run: `cd apps/web && npx --yes pnpm@9 test -- CreditosPanel`
Expected: PASS (5/5).

- [ ] **Step 6: Página**

Criar `apps/web/src/app/creditos/page.tsx`:

```tsx
import { CreditosPanel } from "@/components/CreditosPanel";
import { TenantNav } from "@/components/TenantNav";
import { API_URL } from "@/lib/backend";
import type { CreditPackage } from "@/lib/types";

async function getPackages(): Promise<CreditPackage[]> {
  try {
    const response = await fetch(`${API_URL}/api/v1/credit-packages`, { cache: "no-store" });
    if (!response.ok) return [];
    return response.json();
  } catch {
    return [];
  }
}

export default async function CreditosPage({
  searchParams,
}: {
  searchParams: Promise<{ session_id?: string }>;
}) {
  const [packages, { session_id }] = await Promise.all([getPackages(), searchParams]);

  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="creditos" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <CreditosPanel packages={packages} sessionId={session_id ?? null} />
      </main>
    </div>
  );
}
```

- [ ] **Step 7: Rodar toda a suíte, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde; build lista `/creditos`.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/lib/backend.ts apps/web/src/components/CreditosPanel.tsx apps/web/src/app/creditos/page.tsx apps/web/__tests__/CreditosPanel.test.tsx
git commit -m "feat(web): página de recompra de créditos (/creditos)"
```

---

### Task 5: Atualizar `CLAUDE.md` e verificação local

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: tudo das tasks anteriores.

- [ ] **Step 1: Atualizar o CLAUDE.md**

- Seção "Frontend": no item de `/billing` (ou `/creditos`) — hoje descrito como pendente ("falta só essa tela de recompra") — marcar como ✅ implementado: `/creditos` mostra saldo + pacotes, compra dispara Stripe Checkout, retorno faz polling até confirmar.
- Seção "Billing / Créditos": no parágrafo do cadastro self-service, acrescentar que o mesmo webhook agora também atende recompra de tenants existentes (`flow="recompra"` na metadata), citando as rotas novas (`GET/POST /api/v1/billing/{balance,checkout,status}`).
- Seção "Pendências / próximos tópicos a detalhar": remover a menção a "`/billing` (recompra de créditos)" da lista de pendências, já que passa a estar implementado.

- [ ] **Step 2: Build e verificação local**

```bash
docker compose up -d --build api web
```

1. `curl -s http://localhost:8000/api/v1/credit-packages` — confirma os 4 pacotes.
2. Login com o tenant de seed (`admin@demo.com`/`segredo123`), pegar o `access_token`.
3. `curl http://localhost:8000/api/v1/billing/balance -H "Authorization: Bearer <token>"` — confirma o saldo atual do tenant.
4. `curl -X POST http://localhost:8000/api/v1/billing/checkout -H "Authorization: Bearer <token>" -H 'content-type: application/json' -d '{"credit_package_id":"<id de um pacote>"}'` — confirma `200` com uma `checkout_url` real da Stripe.
5. Com o `stripe listen` rodando (`stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe`), completar o pagamento no navegador com o cartão de teste `4242 4242 4242 4242`.
6. Confirmar no log do `stripe listen` que o evento `checkout.session.completed` voltou `200`.
7. `curl http://localhost:8000/api/v1/billing/balance -H "Authorization: Bearer <token>"` de novo — confirmar que o saldo aumentou exatamente pelo `credits_granted` do pacote comprado.
8. Via `psql`, confirmar que **nenhum** `user`/`tenant` novo foi criado (a recompra não cria conta nova, só credita a existente) e que a `credit_transactions` nova tem `tenant_id` do tenant de seed.
9. Acessar `http://localhost:3001/creditos` logado — confirmar que o saldo e os 4 pacotes aparecem, clicar "Comprar" redireciona pra Stripe.
10. Sem sessão, acessar `/creditos` — confirmar redirect pro `/login` (middleware já cobre `pathname !== "/login" && !hasSession`, nenhuma mudança necessária).

Expected: todos os passos funcionam; o passo 8 (nenhuma conta nova criada) é o mais importante — prova que a recompra realmente credita o tenant existente, sem duplicar cadastro.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: recompra de créditos (/creditos) documentada no CLAUDE.md"
```
