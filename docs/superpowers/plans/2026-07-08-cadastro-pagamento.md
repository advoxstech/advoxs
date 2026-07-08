# Cadastro Self-Service com Pagamento (Stripe) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que um novo escritório se cadastre sozinho — escolhe um pacote de créditos na página inicial, paga via Stripe Checkout, e um `tenant`+`user` são criados automaticamente quando o pagamento é confirmado.

**Architecture:** Endpoint público cria uma Stripe Checkout Session (modo `payment`) guardando os dados do cadastro na `metadata` da sessão — nada persiste antes do pagamento. O webhook da Stripe (`checkout.session.completed`) lê essa `metadata` e cria `tenant`+`user`+`credit_transaction` numa transação. A página inicial (`/`) vira pública para visitantes sem sessão; uma página de sucesso faz polling curto até a conta ficar pronta.

**Tech Stack:** FastAPI + SQLAlchemy async + `stripe` SDK (api), Next.js 15 App Router Server Actions (web), pytest + Vitest.

## Global Constraints

- Modelo pré-pago, sem assinatura recorrente — Stripe Checkout em modo `payment`, não `subscription`. Sem mudança nenhuma no schema de `credit_packages`/`credit_transactions` (já existem).
- `tenant`/`user` só são criados pelo webhook, após pagamento confirmado — nunca no request de criação da sessão.
- Senha nunca sai do backend em texto puro — só o hash (bcrypt, `hash_password` já existente) vai pra `metadata` da Stripe.
- Idempotência do webhook pela `id` da Checkout Session (`cs_...`), guardada em `credit_transactions.stripe_payment_id` — mesmo `session_id` processado de novo é no-op.
- `GET /signup/status` é público e não expõe nenhum dado do tenant, só `{ready: bool}`.
- CNPJ não faz parte do formulário de cadastro (fica de fora, campo já é nullable). Sem verificação de e-mail (login liberado direto após o webhook confirmar).
- Página inicial (`/`) mostra os planos + formulário direto — sem landing separada.
- Pacotes reais a seed(migration de dados): Starter R$100,00/1.000 créditos, Growth R$250,00/2.750 créditos, Scale R$500,00/6.000 créditos, Enterprise R$1.000,00/13.000 créditos — todos `active=true`.
- Mensagens/comentários em pt-BR com acentuação correta.
- Commits: Conventional Commits em pt-BR. Testes: `uv run pytest tests/unit` (api), `pnpm test` (web). Lint: `uv run ruff check .` (api).
- **Sem mudança no `docker-compose.yml`**: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` e `WEB_APP_URL` chegam ao container `api` via `env_file: .env` (mesmo valor em qualquer ambiente, sem override de rede — diferente de `RAG_API_URL`, que muda entre host e container).

---

### Task 1: `api` — fundação de billing (config, migração, schemas, service)

**Files:**
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/.env.example` (raiz do monorepo, `.env.example`)
- Create: `apps/api/alembic/versions/0003_seed_credit_packages.py`
- Create: `apps/api/app/schemas/signup.py`
- Create: `apps/api/app/services/billing.py`
- Test: `apps/api/tests/unit/test_billing_service.py`

**Interfaces:**
- Consumes: `hash_password` de `app/core/security.py`; `Tenant`, `User`, `CreditPackage`, `CreditTransaction` de `app/models` (todos já existentes, sem mudança de schema).
- Produces: `class EmailAlreadyExistsError(Exception)`, `class InvalidPackageError(Exception)`, `class StripeApiError(Exception)`; `async def create_checkout_session(session, tenant_name: str, email: str, password: str, credit_package_id: uuid.UUID) -> str` (retorna a `checkout_url`); `async def process_checkout_completed(session, stripe_session: dict) -> None`; schemas `CreditPackageOut`, `SignupCheckoutRequest`, `CheckoutUrlOut`, `SignupStatusOut`.

- [ ] **Step 1: Config**

Em `apps/api/app/core/config.py`, adicionar ao final da classe `Settings` (antes da linha `settings = Settings()`):

```python
    # Stripe (cadastro self-service — checkout de créditos, sem assinatura)
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # URL pública do `web`, usada para montar success_url/cancel_url do Checkout.
    web_app_url: str = "http://localhost:3000"
```

- [ ] **Step 2: Dependência**

Em `apps/api/pyproject.toml`, adicionar ao array `dependencies` (ordem alfabética, junto das demais):

```toml
    "stripe>=10.0.0",
```

Run: `cd apps/api && uv sync`
Expected: instala o pacote `stripe` sem erro.

- [ ] **Step 3: `.env.example`**

No `.env.example` da raiz do monorepo, confirmar que `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` já existem (devem estar lá, adicionados anteriormente) e adicionar, na mesma seção:

```
# URL pública do web, usada para montar as URLs de retorno do Stripe Checkout
WEB_APP_URL=http://localhost:3000
```

- [ ] **Step 4: Migração — seed dos pacotes de crédito**

Criar `apps/api/alembic/versions/0003_seed_credit_packages.py`:

```python
"""seed dos pacotes de créditos (Starter/Growth/Scale/Enterprise)

Dado de referência que precisa existir de forma idêntica em qualquer
ambiente — cadastro self-service depende desses 4 pacotes existirem.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-08
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

credit_packages = sa.table(
    "credit_packages",
    sa.column("id", sa.Uuid()),
    sa.column("name", sa.String()),
    sa.column("price_brl", sa.Numeric(10, 2)),
    sa.column("credits_granted", sa.Integer()),
    sa.column("active", sa.Boolean()),
)

PACKAGES = [
    {"name": "Starter", "price_brl": "100.00", "credits_granted": 1000},
    {"name": "Growth", "price_brl": "250.00", "credits_granted": 2750},
    {"name": "Scale", "price_brl": "500.00", "credits_granted": 6000},
    {"name": "Enterprise", "price_brl": "1000.00", "credits_granted": 13000},
]


def upgrade() -> None:
    op.bulk_insert(
        credit_packages,
        [
            {
                "id": uuid.uuid4(),
                "name": p["name"],
                "price_brl": p["price_brl"],
                "credits_granted": p["credits_granted"],
                "active": True,
            }
            for p in PACKAGES
        ],
    )


def downgrade() -> None:
    op.execute(
        credit_packages.delete().where(
            credit_packages.c.name.in_([p["name"] for p in PACKAGES])
        )
    )
```

- [ ] **Step 5: Schemas**

Criar `apps/api/app/schemas/signup.py`:

```python
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CreditPackageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    price_brl: Decimal
    credits_granted: int


class SignupCheckoutRequest(BaseModel):
    tenant_name: str = Field(min_length=1)
    email: EmailStr
    password: str = Field(min_length=8)
    credit_package_id: uuid.UUID


class CheckoutUrlOut(BaseModel):
    checkout_url: str


class SignupStatusOut(BaseModel):
    ready: bool
```

(`Literal` fica sem uso aqui — remover do import se o ruff reclamar; nenhum campo desta rodada precisa dele.)

- [ ] **Step 6: Escrever os testes que falham**

Criar `apps/api/tests/unit/test_billing_service.py`:

```python
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.billing as billing
from app.services.billing import (
    EmailAlreadyExistsError,
    InvalidPackageError,
    StripeApiError,
    create_checkout_session,
    process_checkout_completed,
)

PACKAGE_ID = uuid.uuid4()


def _package(active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=PACKAGE_ID,
        name="Growth",
        price_brl=Decimal("250.00"),
        credits_granted=2750,
        active=active,
    )


@pytest.fixture
def session():
    return AsyncMock()


class TestCreateCheckoutSession:
    async def test_email_ja_cadastrado_levanta_erro(self, session) -> None:
        session.scalar.return_value = uuid.uuid4()

        with pytest.raises(EmailAlreadyExistsError):
            await create_checkout_session(
                session, "Escritório", "a@b.com", "senha1234", PACKAGE_ID
            )

    async def test_pacote_inexistente_levanta_erro(self, session) -> None:
        session.scalar.return_value = None
        session.get.return_value = None

        with pytest.raises(InvalidPackageError):
            await create_checkout_session(
                session, "Escritório", "a@b.com", "senha1234", PACKAGE_ID
            )

    async def test_pacote_inativo_levanta_erro(self, session) -> None:
        session.scalar.return_value = None
        session.get.return_value = _package(active=False)

        with pytest.raises(InvalidPackageError):
            await create_checkout_session(
                session, "Escritório", "a@b.com", "senha1234", PACKAGE_ID
            )

    async def test_sucesso_cria_sessao_com_metadata_correta(self, session, monkeypatch) -> None:
        session.scalar.return_value = None
        session.get.return_value = _package()

        created = MagicMock(
            return_value=SimpleNamespace(url="https://checkout.stripe.com/pay/cs_123")
        )
        monkeypatch.setattr(billing.stripe.checkout.Session, "create", created)

        url = await create_checkout_session(
            session, "Escritório Teste", "a@b.com", "senha1234", PACKAGE_ID
        )

        assert url == "https://checkout.stripe.com/pay/cs_123"
        kwargs = created.call_args.kwargs
        assert kwargs["mode"] == "payment"
        assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 25000
        metadata = kwargs["metadata"]
        assert metadata["tenant_name"] == "Escritório Teste"
        assert metadata["email"] == "a@b.com"
        assert metadata["credit_package_id"] == str(PACKAGE_ID)
        assert "password_hash" in metadata
        assert metadata["password_hash"] != "senha1234"

    async def test_falha_na_stripe_levanta_stripe_api_error(self, session, monkeypatch) -> None:
        session.scalar.return_value = None
        session.get.return_value = _package()

        def _raise(*args, **kwargs):
            raise billing.stripe.error.StripeError("falhou")

        monkeypatch.setattr(billing.stripe.checkout.Session, "create", _raise)

        with pytest.raises(StripeApiError):
            await create_checkout_session(
                session, "Escritório", "a@b.com", "senha1234", PACKAGE_ID
            )


class TestProcessCheckoutCompleted:
    def _stripe_session(self, **metadata_overrides) -> dict:
        metadata = {
            "tenant_name": "Escritório Teste",
            "email": "a@b.com",
            "password_hash": "hash-fake",
            "credit_package_id": str(PACKAGE_ID),
        }
        metadata.update(metadata_overrides)
        return {"id": "cs_123", "metadata": metadata}

    async def test_ja_processado_nao_faz_nada(self, session) -> None:
        session.scalar.return_value = uuid.uuid4()

        await process_checkout_completed(session, self._stripe_session())

        session.add.assert_not_called()

    async def test_cria_tenant_user_e_transacao(self, session) -> None:
        session.scalar.return_value = None
        session.get.return_value = _package()
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

        session.flush = AsyncMock(side_effect=fake_flush)

        await process_checkout_completed(session, self._stripe_session())

        assert len(added) == 3
        tenant, user, transaction = added
        assert tenant.name == "Escritório Teste"
        assert tenant.credit_balance == 2750
        assert user.email == "a@b.com"
        assert user.password_hash == "hash-fake"
        assert user.role == "admin"
        assert user.tenant_id == tenant.id
        assert transaction.amount_credits == 2750
        assert transaction.stripe_payment_id == "cs_123"
        session.commit.assert_awaited_once()

    async def test_metadata_incompleta_nao_processa(self, session) -> None:
        session.scalar.return_value = None

        await process_checkout_completed(session, {"id": "cs_123", "metadata": {}})

        session.add.assert_not_called()

    async def test_pacote_nao_encontrado_nao_processa(self, session) -> None:
        session.scalar.return_value = None
        session.get.return_value = None

        await process_checkout_completed(session, self._stripe_session())

        session.add.assert_not_called()

    async def test_integrity_error_no_commit_e_tratado(self, session) -> None:
        session.scalar.return_value = None
        session.get.return_value = _package()
        session.add = MagicMock()

        async def fake_flush():
            pass

        session.flush = AsyncMock(side_effect=fake_flush)
        session.commit = AsyncMock(
            side_effect=billing.IntegrityError("stmt", {}, Exception("dup"))
        )
        session.rollback = AsyncMock()

        await process_checkout_completed(session, self._stripe_session())

        session.rollback.assert_awaited_once()
```

- [ ] **Step 7: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_billing_service.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'app.services.billing'`.

- [ ] **Step 8: Implementar o service**

Criar `apps/api/app/services/billing.py`:

```python
"""Checkout de créditos (Stripe) e provisionamento do tenant após pagamento.

Nada é persistido antes do pagamento confirmar: create_checkout_session só
valida e cria a sessão na Stripe, guardando os dados do cadastro na
metadata; process_checkout_completed (chamado pelo webhook) é quem de fato
cria tenant/user/credit_transaction.
"""

import asyncio
import logging
import uuid

import stripe
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.models import CreditPackage, CreditTransaction, Tenant, User

logger = logging.getLogger(__name__)

stripe.api_key = settings.stripe_secret_key


class EmailAlreadyExistsError(Exception):
    """E-mail já usado por outra conta — mapeado para 409 na rota."""


class InvalidPackageError(Exception):
    """Pacote de créditos inexistente ou inativo — mapeado para 400 na rota."""


class StripeApiError(Exception):
    """Falha ao criar a sessão de checkout na Stripe (rede ou resposta de erro)."""


async def create_checkout_session(
    session: AsyncSession,
    tenant_name: str,
    email: str,
    password: str,
    credit_package_id: uuid.UUID,
) -> str:
    existing = await session.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise EmailAlreadyExistsError(
            "Este e-mail já está cadastrado — faça login ou use outro e-mail"
        )

    package = await session.get(CreditPackage, credit_package_id)
    if package is None or not package.active:
        raise InvalidPackageError("Pacote de créditos inválido")

    try:
        checkout_session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            mode="payment",
            payment_method_types=["card"],
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
                "tenant_name": tenant_name,
                "email": email,
                "password_hash": hash_password(password),
                "credit_package_id": str(credit_package_id),
            },
            success_url=f"{settings.web_app_url}/cadastro/sucesso?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{settings.web_app_url}/cadastro/cancelado",
        )
    except stripe.error.StripeError as exc:
        logger.error("Falha ao criar sessão de checkout | erro=%s", exc)
        raise StripeApiError(
            "Falha ao iniciar o pagamento — tente novamente em instantes"
        ) from exc

    return checkout_session.url


async def process_checkout_completed(session: AsyncSession, stripe_session: dict) -> None:
    """Cria tenant+user+credit_transaction a partir da metadata da sessão paga.

    Idempotente: uma sessão já processada (mesmo id) não cria duplicata.
    """
    session_id = stripe_session["id"]
    already_processed = await session.scalar(
        select(CreditTransaction.id).where(CreditTransaction.stripe_payment_id == session_id)
    )
    if already_processed is not None:
        logger.info("Webhook duplicado, ignorando | session=%s", session_id)
        return

    metadata = stripe_session.get("metadata") or {}
    tenant_name = metadata.get("tenant_name")
    email = metadata.get("email")
    password_hash = metadata.get("password_hash")
    credit_package_id = metadata.get("credit_package_id")
    if not all([tenant_name, email, password_hash, credit_package_id]):
        logger.error(
            "Metadata incompleta no checkout.session.completed | session=%s", session_id
        )
        return

    package = await session.get(CreditPackage, uuid.UUID(credit_package_id))
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
```

- [ ] **Step 9: Rodar os testes e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Expected: todos os testes PASS (inclusive os pré-existentes), ruff sem erros. Se o ruff reclamar de `Literal` sem uso em `schemas/signup.py`, remover esse import.

- [ ] **Step 10: Validar a migração**

Run (ajustar credenciais conforme seu Postgres local, mesmo padrão das migrations anteriores):

```bash
DATABASE_URL="postgresql+asyncpg://advoxs:changeme@localhost:5433/advoxs" REDIS_URL="redis://localhost:6379/0" JWT_SECRET="test" uv run alembic upgrade head
```

Expected: aplica a `0003` sem erro. Conferir que os 4 pacotes existem: `psql` ou uma query simples `SELECT name, credits_granted FROM credit_packages;` deve retornar as 4 linhas.

- [ ] **Step 11: Commit**

```bash
git add apps/api/app/core/config.py apps/api/pyproject.toml apps/api/uv.lock .env.example apps/api/alembic/versions/0003_seed_credit_packages.py apps/api/app/schemas/signup.py apps/api/app/services/billing.py apps/api/tests/unit/test_billing_service.py
git commit -m "feat(api): fundação de billing — checkout Stripe e provisionamento de tenant"
```

---

### Task 2: `api` — rotas (`credit-packages`, `signup`, webhook da Stripe)

**Files:**
- Create: `apps/api/app/api/v1/credit_packages.py`
- Create: `apps/api/app/api/v1/signup.py`
- Create: `apps/api/app/api/v1/webhooks/stripe.py`
- Modify: `apps/api/app/api/v1/router.py`
- Test: `apps/api/tests/unit/test_credit_packages_routes.py`
- Test: `apps/api/tests/unit/test_signup_routes.py`
- Test: `apps/api/tests/unit/test_stripe_webhook.py`

**Interfaces:**
- Consumes: `create_checkout_session`, `process_checkout_completed`, `EmailAlreadyExistsError`, `InvalidPackageError`, `StripeApiError` da Task 1 (`app.services.billing`); `CreditPackageOut`, `SignupCheckoutRequest`, `CheckoutUrlOut`, `SignupStatusOut` da Task 1 (`app.schemas.signup`); `get_session` de `app.core.db` (já existente, sem tenant/RLS — usado em rotas públicas).
- Produces: `GET /api/v1/credit-packages` → `list[CreditPackageOut]`; `POST /api/v1/signup/checkout` → `CheckoutUrlOut` (200); `GET /api/v1/signup/status?session_id=` → `SignupStatusOut`; `POST /api/v1/webhooks/stripe` → `dict`.

- [ ] **Step 1: Escrever os testes que falham**

Criar `apps/api/tests/unit/test_credit_packages_routes.py`:

```python
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.core.db import get_session
from app.main import app


def _package(name: str, active: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        price_brl=Decimal("100.00"),
        credits_granted=1000,
        active=active,
    )


def test_lista_so_pacotes_ativos() -> None:
    session = AsyncMock()
    result = AsyncMock()
    result.scalars.return_value.all.return_value = [_package("Starter", True)]
    session.execute.return_value = result

    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).get("/api/v1/credit-packages")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Starter"
```

Criar `apps/api/tests/unit/test_signup_routes.py`:

```python
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.signup as signup_module
from app.core.db import get_session
from app.main import app
from app.services.billing import EmailAlreadyExistsError, InvalidPackageError, StripeApiError

PACKAGE_ID = uuid.uuid4()

CHECKOUT_BODY = {
    "tenant_name": "Escritório Teste",
    "email": "a@b.com",
    "password": "senha1234",
    "credit_package_id": str(PACKAGE_ID),
}


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture
def client(session):
    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestCheckout:
    def test_sucesso_retorna_checkout_url(self, client, monkeypatch) -> None:
        create = AsyncMock(return_value="https://checkout.stripe.com/pay/cs_123")
        monkeypatch.setattr(signup_module, "create_checkout_session", create)

        response = client.post("/api/v1/signup/checkout", json=CHECKOUT_BODY)

        assert response.status_code == 200
        assert response.json()["checkout_url"] == "https://checkout.stripe.com/pay/cs_123"

    def test_email_duplicado_retorna_409(self, client, monkeypatch) -> None:
        create = AsyncMock(side_effect=EmailAlreadyExistsError("já cadastrado"))
        monkeypatch.setattr(signup_module, "create_checkout_session", create)

        response = client.post("/api/v1/signup/checkout", json=CHECKOUT_BODY)

        assert response.status_code == 409

    def test_pacote_invalido_retorna_400(self, client, monkeypatch) -> None:
        create = AsyncMock(side_effect=InvalidPackageError("pacote inválido"))
        monkeypatch.setattr(signup_module, "create_checkout_session", create)

        response = client.post("/api/v1/signup/checkout", json=CHECKOUT_BODY)

        assert response.status_code == 400

    def test_falha_stripe_retorna_502(self, client, monkeypatch) -> None:
        create = AsyncMock(side_effect=StripeApiError("falhou"))
        monkeypatch.setattr(signup_module, "create_checkout_session", create)

        response = client.post("/api/v1/signup/checkout", json=CHECKOUT_BODY)

        assert response.status_code == 502

    def test_senha_curta_retorna_422(self, client) -> None:
        body = {**CHECKOUT_BODY, "password": "curta"}

        response = client.post("/api/v1/signup/checkout", json=body)

        assert response.status_code == 422


class TestStatus:
    def test_ready_quando_transacao_existe(self, client, session) -> None:
        session.scalar.return_value = uuid.uuid4()

        response = client.get("/api/v1/signup/status", params={"session_id": "cs_123"})

        assert response.status_code == 200
        assert response.json() == {"ready": True}

    def test_not_ready_quando_transacao_nao_existe(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.get("/api/v1/signup/status", params={"session_id": "cs_123"})

        assert response.json() == {"ready": False}
```

Criar `apps/api/tests/unit/test_stripe_webhook.py`:

```python
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.webhooks.stripe as stripe_webhook_module
from app.core.db import get_session
from app.main import app


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture
def client(session):
    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_assinatura_invalida_retorna_400(client, monkeypatch) -> None:
    def _raise(*args, **kwargs):
        raise stripe_webhook_module.stripe.error.SignatureVerificationError("inválida", "sig")

    monkeypatch.setattr(stripe_webhook_module.stripe.Webhook, "construct_event", _raise)

    response = client.post(
        "/api/v1/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "sig-invalida"}
    )

    assert response.status_code == 400


def test_checkout_completed_processa_evento(client, monkeypatch) -> None:
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_123", "metadata": {}}},
    }
    monkeypatch.setattr(
        stripe_webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event
    )
    process = AsyncMock()
    monkeypatch.setattr(stripe_webhook_module, "process_checkout_completed", process)

    response = client.post(
        "/api/v1/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "sig-valida"}
    )

    assert response.status_code == 200
    process.assert_awaited_once()


def test_evento_diferente_e_ignorado(client, monkeypatch) -> None:
    event = {"type": "payment_intent.succeeded", "data": {"object": {}}}
    monkeypatch.setattr(
        stripe_webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event
    )
    process = AsyncMock()
    monkeypatch.setattr(stripe_webhook_module, "process_checkout_completed", process)

    response = client.post(
        "/api/v1/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "sig-valida"}
    )

    assert response.status_code == 200
    process.assert_not_awaited()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_credit_packages_routes.py tests/unit/test_signup_routes.py tests/unit/test_stripe_webhook.py -v`
Expected: FAIL na coleta — módulos `app.api.v1.credit_packages`/`app.api.v1.signup`/`app.api.v1.webhooks.stripe` não existem.

- [ ] **Step 3: Rota de listagem**

Criar `apps/api/app/api/v1/credit_packages.py`:

```python
"""Listagem pública dos pacotes de créditos à venda (cadastro self-service)."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import CreditPackage
from app.schemas.signup import CreditPackageOut

router = APIRouter(prefix="/credit-packages", tags=["signup"])


@router.get("")
async def list_credit_packages(
    session: AsyncSession = Depends(get_session),
) -> list[CreditPackageOut]:
    result = await session.execute(
        select(CreditPackage)
        .where(CreditPackage.active.is_(True))
        .order_by(CreditPackage.price_brl)
    )
    return [CreditPackageOut.model_validate(p) for p in result.scalars().all()]
```

- [ ] **Step 4: Rotas de cadastro**

Criar `apps/api/app/api/v1/signup.py`:

```python
"""Cadastro self-service: cria a sessão de checkout e informa quando o tenant fica pronto."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import CreditTransaction
from app.schemas.signup import CheckoutUrlOut, SignupCheckoutRequest, SignupStatusOut
from app.services.billing import (
    EmailAlreadyExistsError,
    InvalidPackageError,
    StripeApiError,
    create_checkout_session,
)

router = APIRouter(prefix="/signup", tags=["signup"])


@router.post("/checkout")
async def checkout(
    body: SignupCheckoutRequest,
    session: AsyncSession = Depends(get_session),
) -> CheckoutUrlOut:
    try:
        checkout_url = await create_checkout_session(
            session, body.tenant_name, body.email, body.password, body.credit_package_id
        )
    except EmailAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except InvalidPackageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except StripeApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return CheckoutUrlOut(checkout_url=checkout_url)


@router.get("/status")
async def signup_status(
    session_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> SignupStatusOut:
    found = await session.scalar(
        select(CreditTransaction.id).where(CreditTransaction.stripe_payment_id == session_id)
    )
    return SignupStatusOut(ready=found is not None)
```

- [ ] **Step 5: Webhook da Stripe**

Criar `apps/api/app/api/v1/webhooks/stripe.py` (mesmo pacote `webhooks` de `whatsapp.py`, arquivo novo ao lado):

```python
"""Webhook da Stripe: confirmação de pagamento do cadastro self-service."""

import logging

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.services.billing import process_checkout_completed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/stripe", tags=["webhooks"])


@router.post("")
async def receive_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    raw_body = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            raw_body, stripe_signature, settings.stripe_webhook_secret
        )
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        logger.warning("Assinatura de webhook inválida | erro=%s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Assinatura inválida")

    if event["type"] == "checkout.session.completed":
        await process_checkout_completed(session, event["data"]["object"])

    return {"status": "ok"}
```

- [ ] **Step 6: Registrar no router principal**

Em `apps/api/app/api/v1/router.py`, adicionar os imports e includes (junto dos demais):

```python
from app.api.v1.credit_packages import router as credit_packages_router
from app.api.v1.signup import router as signup_router
from app.api.v1.webhooks.stripe import router as stripe_webhook_router
```

```python
api_router.include_router(credit_packages_router)
api_router.include_router(signup_router)
api_router.include_router(stripe_webhook_router)
```

- [ ] **Step 7: Rodar os testes e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Expected: todos PASS, ruff limpo.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/api/v1/credit_packages.py apps/api/app/api/v1/signup.py apps/api/app/api/v1/webhooks/stripe.py apps/api/app/api/v1/router.py apps/api/tests/unit/test_credit_packages_routes.py apps/api/tests/unit/test_signup_routes.py apps/api/tests/unit/test_stripe_webhook.py
git commit -m "feat(api): rotas de cadastro self-service (planos, checkout, webhook da Stripe)"
```

---

### Task 3: `web` — página inicial com planos e formulário de cadastro

**Files:**
- Modify: `apps/web/src/lib/backend.ts`
- Modify: `apps/web/src/lib/types.ts`
- Create: `apps/web/src/app/actions.ts`
- Create: `apps/web/src/components/SignupForm.tsx`
- Modify: `apps/web/src/app/page.tsx`
- Test: `apps/web/__tests__/backend.test.ts`
- Test: `apps/web/__tests__/signup-actions.test.ts`

**Interfaces:**
- Consumes: `API_URL` de `@/lib/backend` (fetch server-side direto, mesmo padrão de `apps/web/src/app/login/actions.ts`); rotas `GET /credit-packages` e `POST /signup/checkout` da Task 2.
- Produces: `export async function signup(prev: SignupState, formData: FormData): Promise<SignupState>` em `@/app/actions`; `export type SignupState = { error: string | null }`; componente `SignupForm({ packages: CreditPackage[] })`; tipo `CreditPackage` em `@/lib/types`.

- [ ] **Step 1: Teste que falha (allowlist)**

Em `apps/web/__tests__/backend.test.ts`, adicionar dentro do `describe("isAllowedPath", ...)`:

```ts
  it("permite rotas de signup", () => {
    expect(isAllowedPath(["signup", "status"])).toBe(true);
  });
```

Run: `cd apps/web && npx --yes pnpm@9 test -- backend`
Expected: FAIL — `"signup"` não está na allowlist.

- [ ] **Step 2: Allowlist**

Em `apps/web/src/lib/backend.ts`:

```ts
const ALLOWED_PREFIXES = ["conversations", "knowledge-base", "whatsapp", "signup"];
```

(Nota: `signup/checkout` e `credit-packages` NUNCA passam por esse proxy — são chamados direto de Server Actions/Server Components no servidor, com `API_URL`. Só `signup/status`, chamado do browser pra fazer polling na Task 4, precisa da allowlist.)

Run: `cd apps/web && npx --yes pnpm@9 test -- backend` → PASS.

- [ ] **Step 3: Tipo `CreditPackage`**

Em `apps/web/src/lib/types.ts`, adicionar ao final:

```ts
export interface CreditPackage {
  id: string;
  name: string;
  price_brl: number;
  credits_granted: number;
}
```

- [ ] **Step 4: Teste da Server Action que falha**

Criar `apps/web/__tests__/signup-actions.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  redirect: vi.fn(),
}));

import { redirect } from "next/navigation";

import { signup } from "@/app/actions";

const mockedRedirect = redirect as ReturnType<typeof vi.fn>;
const mockedFetch = vi.fn();

beforeEach(() => {
  mockedRedirect.mockReset();
  mockedFetch.mockReset();
  vi.stubGlobal("fetch", mockedFetch);
});

function formData(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) data.append(key, value);
  return data;
}

describe("signup action", () => {
  it("redireciona para o checkout_url em caso de sucesso", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ checkout_url: "https://checkout.stripe.com/pay/cs_123" }),
    });

    await signup(
      { error: null },
      formData({
        tenant_name: "Escritório Teste",
        email: "a@b.com",
        password: "senha1234",
        credit_package_id: "pkg-1",
      }),
    );

    expect(mockedRedirect).toHaveBeenCalledWith("https://checkout.stripe.com/pay/cs_123");
  });

  it("retorna a mensagem de erro (string) quando a API rejeita", async () => {
    mockedFetch.mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "Este e-mail já está cadastrado" }),
    });

    const result = await signup({ error: null }, formData({ email: "a@b.com" }));

    expect(result.error).toBe("Este e-mail já está cadastrado");
    expect(mockedRedirect).not.toHaveBeenCalled();
  });

  it("usa mensagem padrão quando detail não é string (ex: erro 422 em array)", async () => {
    mockedFetch.mockResolvedValue({
      ok: false,
      json: async () => ({
        detail: [{ type: "string_too_short", loc: ["body", "password"] }],
      }),
    });

    const result = await signup({ error: null }, formData({ email: "a@b.com" }));

    expect(result.error).toBe("Não foi possível iniciar o pagamento. Tente novamente.");
  });

  it("trata falha de rede", async () => {
    mockedFetch.mockRejectedValue(new Error("network down"));

    const result = await signup({ error: null }, formData({ email: "a@b.com" }));

    expect(result.error).toBe("Não foi possível conectar ao servidor. Tente novamente.");
  });
});
```

Run: `cd apps/web && npx --yes pnpm@9 test -- signup-actions`
Expected: FAIL — `@/app/actions` não existe.

- [ ] **Step 5: Server Action**

Criar `apps/web/src/app/actions.ts`:

```ts
"use server";

import { redirect } from "next/navigation";

import { API_URL } from "@/lib/backend";

export interface SignupState {
  error: string | null;
}

export async function signup(_prev: SignupState, formData: FormData): Promise<SignupState> {
  const tenant_name = String(formData.get("tenant_name") ?? "");
  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");
  const credit_package_id = String(formData.get("credit_package_id") ?? "");

  let checkoutUrl: string;
  try {
    const response = await fetch(`${API_URL}/api/v1/signup/checkout`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ tenant_name, email, password, credit_package_id }),
      cache: "no-store",
    });

    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const detail = typeof body?.detail === "string" ? body.detail : null;
      return { error: detail ?? "Não foi possível iniciar o pagamento. Tente novamente." };
    }
    const parsed = await response.json();
    checkoutUrl = parsed.checkout_url;
  } catch {
    return { error: "Não foi possível conectar ao servidor. Tente novamente." };
  }

  redirect(checkoutUrl);
}
```

- [ ] **Step 6: Rodar o teste da action**

Run: `cd apps/web && npx --yes pnpm@9 test -- signup-actions`
Expected: PASS (4/4).

- [ ] **Step 7: Formulário**

Criar `apps/web/src/components/SignupForm.tsx`:

```tsx
"use client";

import { useActionState } from "react";

import { signup, type SignupState } from "@/app/actions";
import type { CreditPackage } from "@/lib/types";

const initialState: SignupState = { error: null };

export function SignupForm({ packages }: { packages: CreditPackage[] }) {
  const [state, formAction, pending] = useActionState(signup, initialState);

  return (
    <form action={formAction} className="flex flex-col gap-6">
      <fieldset className="flex flex-col gap-3">
        <legend className="text-sm font-medium text-ink">Escolha um plano</legend>
        {packages.map((pkg, index) => (
          <label
            key={pkg.id}
            className="flex items-center justify-between gap-4 rounded-sm border border-line bg-surface px-4 py-3 text-sm"
          >
            <span className="flex items-center gap-3">
              <input type="radio" name="credit_package_id" value={pkg.id} required defaultChecked={index === 0} />
              <span>
                <span className="font-medium text-ink">{pkg.name}</span>{" "}
                <span className="text-muted">— {pkg.credits_granted} créditos</span>
              </span>
            </span>
            <span className="font-mono text-xs text-muted">
              R$ {Number(pkg.price_brl).toFixed(2)}
            </span>
          </label>
        ))}
      </fieldset>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="tenant_name" className="text-sm font-medium text-ink">
          Nome do escritório
        </label>
        <input
          id="tenant_name"
          name="tenant_name"
          type="text"
          required
          className="rounded-sm border border-line bg-surface px-3 py-2.5 text-sm"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="email" className="text-sm font-medium text-ink">
          E-mail
        </label>
        <input
          id="email"
          name="email"
          type="email"
          required
          autoComplete="email"
          className="rounded-sm border border-line bg-surface px-3 py-2.5 text-sm"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="password" className="text-sm font-medium text-ink">
          Senha
        </label>
        <input
          id="password"
          name="password"
          type="password"
          required
          minLength={8}
          autoComplete="new-password"
          className="rounded-sm border border-line bg-surface px-3 py-2.5 text-sm"
        />
      </div>

      {state.error ? (
        <p role="alert" className="border-l-2 border-danger pl-3 text-sm text-danger">
          {state.error}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={pending}
        className="mt-1 rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink disabled:opacity-60"
      >
        {pending ? "Preparando pagamento…" : "Assinar e pagar"}
      </button>
    </form>
  );
}
```

- [ ] **Step 8: Página inicial**

Substituir todo o conteúdo de `apps/web/src/app/page.tsx` (hoje só faz `redirect("/conversas")` — deixa de fazer sentido, já que o middleware — ajustado na Task 4 — só deixa authenticados chegarem aqui):

```tsx
import Link from "next/link";

import { SignupForm } from "@/components/SignupForm";
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

export default async function HomePage() {
  const packages = await getPackages();

  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="w-full max-w-md">
        <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-muted">
          Plataforma de agentes de IA
        </p>
        <h1 className="mt-2 font-display text-5xl font-semibold text-ink">
          Advoxs<span className="text-accent">.</span>
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          Agentes de IA que atendem os clientes do seu escritório pelo WhatsApp. Escolha um
          plano e comece agora.
        </p>

        <hr className="my-8 border-line" />

        <SignupForm packages={packages} />

        <p className="mt-6 text-center text-sm text-muted">
          Já tem conta?{" "}
          <Link href="/login" className="text-accent hover:underline">
            Entrar
          </Link>
        </p>
      </div>
    </main>
  );
}
```

- [ ] **Step 9: Teste da página inicial que falha**

Criar `apps/web/__tests__/HomePage.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import HomePage from "@/app/page";

const mockedFetch = vi.fn();

beforeEach(() => {
  mockedFetch.mockReset();
  vi.stubGlobal("fetch", mockedFetch);
});

describe("HomePage", () => {
  it("renderiza os planos a partir do fetch de credit-packages", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        { id: "p1", name: "Starter", price_brl: 100, credits_granted: 1000 },
        { id: "p2", name: "Growth", price_brl: 250, credits_granted: 2750 },
      ],
    });

    render(await HomePage());

    expect(screen.getByText("Starter")).toBeInTheDocument();
    expect(screen.getByText("Growth")).toBeInTheDocument();
  });

  it("renderiza a página mesmo quando o fetch de planos falha", async () => {
    mockedFetch.mockResolvedValue({ ok: false });

    render(await HomePage());

    expect(screen.getByText("Advoxs")).toBeInTheDocument();
  });
});
```

`HomePage` é um Server Component assíncrono — chamá-lo direto (`await HomePage()`) e passar o elemento resultante pro `render()` do Testing Library funciona porque, nesse nível, é só uma função async retornando JSX; não precisa de runtime do Next pra isso.

Run: `cd apps/web && npx --yes pnpm@9 test -- HomePage`
Expected: FAIL — `@/app/page` ainda não tem esse conteúdo (só falha se você estiver rodando este step antes do Step 8; se já implementou o Step 8, ajuste a ordem e rode o teste antes de seguir).

- [ ] **Step 10: Rodar o teste da página inicial**

Run: `cd apps/web && npx --yes pnpm@9 test -- HomePage`
Expected: PASS (2/2).

- [ ] **Step 11: Rodar os testes, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde. O `build` pode falhar se o middleware (ainda não ajustado — Task 4) redirecionar `/` de forma incompatível com prerender; se isso acontecer, é esperado e resolve na Task 4 — não é bloqueio desta task, mas rode `pnpm build` de novo ao final da Task 4 pra confirmar.

- [ ] **Step 12: Commit**

```bash
git add apps/web/src/lib/backend.ts apps/web/src/lib/types.ts apps/web/src/app/actions.ts apps/web/src/components/SignupForm.tsx apps/web/src/app/page.tsx apps/web/__tests__/backend.test.ts apps/web/__tests__/signup-actions.test.ts apps/web/__tests__/HomePage.test.tsx
git commit -m "feat(web): página inicial com planos e formulário de cadastro"
```

---

### Task 4: `web` — páginas de sucesso/cancelado e middleware público

**Files:**
- Create: `apps/web/src/components/SignupSuccessPanel.tsx`
- Create: `apps/web/src/app/cadastro/sucesso/page.tsx`
- Create: `apps/web/src/app/cadastro/cancelado/page.tsx`
- Modify: `apps/web/src/middleware.ts`
- Test: `apps/web/__tests__/SignupSuccessPanel.test.tsx`

**Interfaces:**
- Consumes: `backendFetch` de `@/lib/client-api`; rota `GET signup/status` da Task 2 (já na allowlist desde a Task 3).
- Produces: componente `SignupSuccessPanel({ sessionId: string | null, pollMs?: number })`; rotas `/cadastro/sucesso` e `/cadastro/cancelado` públicas.

- [ ] **Step 1: Teste que falha**

Criar `apps/web/__tests__/SignupSuccessPanel.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SignupSuccessPanel } from "@/components/SignupSuccessPanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedBackendFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedBackendFetch.mockReset();
});

describe("SignupSuccessPanel", () => {
  it("mostra a mensagem de pronto quando o status confirma", async () => {
    mockedBackendFetch.mockResolvedValue({ ok: true, json: async () => ({ ready: true }) });

    render(<SignupSuccessPanel sessionId="cs_123" />);

    await waitFor(() => expect(screen.getByText("Pagamento confirmado")).toBeInTheDocument());
    expect(screen.getByText("Ir para o login")).toBeInTheDocument();
    expect(mockedBackendFetch).toHaveBeenCalledWith(
      "signup/status?session_id=cs_123",
    );
  });

  it("continua mostrando 'confirmando' enquanto ready é false", async () => {
    mockedBackendFetch.mockResolvedValue({ ok: true, json: async () => ({ ready: false }) });

    render(<SignupSuccessPanel sessionId="cs_123" />);

    await waitFor(() => expect(mockedBackendFetch).toHaveBeenCalled());
    expect(screen.getByText("Confirmando seu pagamento…")).toBeInTheDocument();
  });

  it("sem session_id, mostra o estado de pronto imediatamente (sem polling)", () => {
    render(<SignupSuccessPanel sessionId={null} />);

    expect(screen.getByText("Pagamento confirmado")).toBeInTheDocument();
    expect(mockedBackendFetch).not.toHaveBeenCalled();
  });

  it("mostra o estado de pronto (tom neutro) após esgotar as tentativas sem confirmar", async () => {
    mockedBackendFetch.mockResolvedValue({ ok: true, json: async () => ({ ready: false }) });

    render(<SignupSuccessPanel sessionId="cs_123" pollMs={0} />);

    await waitFor(
      () => expect(screen.getByText("Pagamento confirmado")).toBeInTheDocument(),
      { timeout: 3000 },
    );
    expect(screen.getByText("Ir para o login")).toBeInTheDocument();
  });
});
```

`pollMs={0}` faz o `setInterval` disparar a cada tick do event loop — as 8 tentativas (`MAX_ATTEMPTS`) se esgotam em poucos milissegundos, sem precisar de fake timers nem esperar segundos reais.

Run: `cd apps/web && npx --yes pnpm@9 test -- SignupSuccessPanel`
Expected: FAIL — componente não existe.

- [ ] **Step 2: Componente**

Criar `apps/web/src/components/SignupSuccessPanel.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";

const MAX_ATTEMPTS = 8;

export function SignupSuccessPanel({
  sessionId,
  pollMs = 2000,
}: {
  sessionId: string | null;
  pollMs?: number;
}) {
  const [ready, setReady] = useState(false);
  const [attempts, setAttempts] = useState(0);

  async function checkStatus() {
    if (!sessionId) return;
    try {
      const response = await backendFetch(
        `signup/status?session_id=${encodeURIComponent(sessionId)}`,
      );
      if (response.ok) {
        const body = await response.json();
        if (body.ready) {
          setReady(true);
          return;
        }
      }
    } catch {
      // Rede instável durante o polling — só tenta de novo no próximo ciclo.
    }
    setAttempts((prev) => prev + 1);
  }

  useEffect(() => {
    void checkStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId || ready || attempts >= MAX_ATTEMPTS) return;
    const interval = setInterval(() => void checkStatus(), pollMs);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, ready, attempts, pollMs]);

  const settled = ready || attempts >= MAX_ATTEMPTS || !sessionId;

  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm text-center">
        <h1 className="font-display text-3xl font-semibold text-ink">
          {settled ? "Pagamento confirmado" : "Confirmando seu pagamento…"}
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          {settled
            ? "Sua conta está pronta. Você já pode entrar com o e-mail e a senha que cadastrou."
            : "Isso leva só alguns segundos."}
        </p>
        {settled && (
          <a
            href="/login"
            className="mt-6 inline-block rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink"
          >
            Ir para o login
          </a>
        )}
      </div>
    </main>
  );
}
```

(O `eslint-disable-next-line react-hooks/exhaustive-deps` é necessário porque `checkStatus` é recriada a cada render e não deve entrar na lista de dependências — mesmo padrão implícito já tolerado nos outros painéis do projeto; se o lint reclamar mesmo assim, envolver `checkStatus` num `useCallback` com as mesmas dependências do efeito.)

- [ ] **Step 3: Rodar o teste do componente**

Run: `cd apps/web && npx --yes pnpm@9 test -- SignupSuccessPanel`
Expected: PASS (4/4).

- [ ] **Step 4: Página de sucesso**

Criar `apps/web/src/app/cadastro/sucesso/page.tsx`:

```tsx
import { SignupSuccessPanel } from "@/components/SignupSuccessPanel";

export default async function CadastroSucessoPage({
  searchParams,
}: {
  searchParams: Promise<{ session_id?: string }>;
}) {
  const { session_id } = await searchParams;
  return <SignupSuccessPanel sessionId={session_id ?? null} />;
}
```

- [ ] **Step 5: Página de cancelado**

Criar `apps/web/src/app/cadastro/cancelado/page.tsx`:

```tsx
import Link from "next/link";

export default function CadastroCanceladoPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm text-center">
        <h1 className="font-display text-3xl font-semibold text-ink">Pagamento cancelado</h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          Nenhuma cobrança foi feita. Você pode tentar de novo quando quiser.
        </p>
        <Link
          href="/"
          className="mt-6 inline-block rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink"
        >
          Voltar
        </Link>
      </div>
    </main>
  );
}
```

- [ ] **Step 6: Middleware**

Em `apps/web/src/middleware.ts`, o branch de `pathname === "/"` muda — sem sessão, deixa passar (renderiza a página pública) em vez de redirecionar pro login; com sessão, continua indo pra `/conversas`. Localizar:

```ts
  if (pathname === "/") {
    return NextResponse.redirect(
      new URL(hasSession ? "/conversas" : "/login", request.url),
    );
  }
```

E substituir por:

```ts
  if (pathname === "/") {
    if (hasSession) {
      return NextResponse.redirect(new URL("/conversas", request.url));
    }
    return NextResponse.next();
  }
```

O `matcher` não precisa de mudança — `/cadastro/sucesso` e `/cadastro/cancelado` já ficam fora dele (públicas por padrão, mesmo princípio de `/login`).

- [ ] **Step 7: Rodar os testes, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde, incluindo as rotas `/cadastro/sucesso` e `/cadastro/cancelado` geradas no build.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/components/SignupSuccessPanel.tsx apps/web/src/app/cadastro apps/web/src/middleware.ts apps/web/__tests__/SignupSuccessPanel.test.tsx
git commit -m "feat(web): páginas de sucesso/cancelado do cadastro e página inicial pública"
```

---

### Task 5: Atualizar `CLAUDE.md` e verificação local

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: tudo das tasks anteriores.

- [ ] **Step 1: Atualizar o CLAUDE.md**

Seguindo o estilo das seções existentes:

- Seção "Estado atual do repositório": `api` ganhou `/api/v1/credit-packages`, `/api/v1/signup/{checkout,status}` e `/api/v1/webhooks/stripe`; `web` ganhou a página inicial pública (planos + cadastro) e `/cadastro/{sucesso,cancelado}`.
- Seção "Billing / Créditos": trocar "Webhook do Stripe confirma pagamento → credita o saldo — a implementar" por ✅ implementado, descrevendo o fluxo (Checkout Session em modo `payment`, dados do cadastro na `metadata`, tenant/user/credit_transaction criados só após confirmação, idempotência por `stripe_payment_id`). Marcar os 4 pacotes como seedados via migration (não mais "valores de partida" hipotéticos — já existem no banco).
- Seção "Frontend": adicionar entradas para a página inicial pública e `/cadastro/sucesso`/`/cadastro/cancelado`, descrevendo o fluxo de cadastro self-service.
- Pendências: os itens "margem sobre custo do LLM" e "comportamento quando o saldo zera" continuam abertos (fora do escopo desta entrega) — não remover.

- [ ] **Step 2: Verificação local**

A Stripe não tem um jeito de testar o Checkout de ponta a ponta sem uma chave de teste real (mesmo em modo sandbox, precisa de uma `STRIPE_SECRET_KEY` de teste válida). Validar o que é possível sem ela:

```bash
docker compose up -d --build api web
DATABASE_URL="postgresql+asyncpg://advoxs:changeme@localhost:5433/advoxs" REDIS_URL="redis://localhost:6379/0" JWT_SECRET="test" uv run alembic upgrade head  # dentro de apps/api, garante a migration 0003 aplicada
```

1. `curl http://localhost:8000/api/v1/credit-packages` — deve retornar os 4 pacotes.
2. Acessar `http://localhost:3001/` sem estar logado — deve mostrar a página com os planos e o formulário (não redirecionar pro login).
3. Fazer login normalmente em `/login` com o usuário do seed (`admin@demo.com`) e depois acessar `/` — deve redirecionar pra `/conversas` (comportamento preservado).
4. Acessar `/cadastro/cancelado` direto — deve renderizar sem exigir sessão.
5. Se houver uma `STRIPE_SECRET_KEY` de teste real disponível, submeter o formulário em `/` e confirmar que o navegador é redirecionado pra um checkout real da Stripe (`checkout.stripe.com`). Sem a chave, `POST /signup/checkout` retorna `502` (Stripe rejeita chave vazia/inválida) — comportamento esperado e correto (não é bug).

Expected: passos 1-4 funcionam sem credenciais reais da Stripe; passo 5 fica documentado como pendente até haver uma chave de teste disponível.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: cadastro self-service com pagamento documentado no CLAUDE.md"
```
