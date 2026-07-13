# Cobrança do Cliente Final — Fundação (`apps/api`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar ao `api` tudo que ele precisa pra tenants cobrarem os próprios clientes finais via Stripe: modelo de dados, criptografia da secret key/webhook secret por tenant, CRUD de configuração/pacotes, criação de Checkout Session dinâmica e processamento do webhook por tenant.

**Architecture:** Espelha o billing tenant→plataforma já existente (`app/services/billing.py`, `app/api/v1/webhooks/stripe.py`), mas com uma segunda camada isolada por tenant: cada tenant guarda a própria secret key/webhook secret (cifradas), define os próprios pacotes, e tem um endpoint de webhook próprio (`/webhooks/stripe/tenant/{tenant_id}`) porque a verificação de assinatura da Stripe exige o secret correto antes de validar. Um endpoint interno novo (`/internal/end-customer-billing/checkout`) permite que o `agents` (Plano 2) peça a criação do link de pagamento sem nunca ver a secret key do tenant.

**Tech Stack:** FastAPI, SQLAlchemy 2 (async), Alembic, `stripe` SDK, `cryptography.fernet`, pytest + pytest-asyncio.

## Global Constraints

- Todas as queries em rota tenant-scoped usam `get_tenant_session` (RLS ativa); rotas cross-tenant (webhook, endpoint interno) usam `get_system_session` (BYPASSRLS) com filtro explícito de `tenant_id` em toda query.
- RLS de toda tabela nova: `CREATE POLICY tenant_isolation ON <tabela> USING (tenant_id = current_setting('app.tenant_id', true)::uuid) WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)` (mesma policy da migration `0001`). Não é preciso repetir `GRANT` — `ALTER DEFAULT PRIVILEGES` da migration `0008` já cobre tabelas novas.
- Secret key/webhook secret do tenant: Fernet, nova env `TENANT_STRIPE_KEY_ENCRYPTION_KEY` (separada de `WHATSAPP_TOKEN_ENCRYPTION_KEY`). Nunca retornadas em nenhuma resposta de API.
- Auth do endpoint interno (`agents` → `api`): nova env `INTERNAL_SERVICE_KEY`, header `Authorization: <INTERNAL_SERVICE_KEY>` — direção oposta de `AGENTS_API_KEY`.
- **Nunca** setar `stripe.api_key` globalmente para a chave de um tenant — cada chamada à Stripe recebe `api_key=<secret_key_do_tenant>` explicitamente no próprio `stripe.checkout.Session.create(...)`/`stripe.Webhook.construct_event(...)`, senão uma requisição concorrente de outro tenant usaria a chave errada.
- Ruff line-length 100. Comandos: `uv run pytest tests/unit -q` e `uv run ruff check .`, sempre dentro de `apps/api`.
- Todo texto/erro voltado a humano em português, seguindo o tom do restante do `api` (ex: mensagens de `HTTPException.detail`).

---

### Task 1: Migration — tabelas novas + RLS + `sender_type` ganha `'system'`

**Files:**
- Create: `apps/api/alembic/versions/0009_end_customer_billing.py`

**Interfaces:**
- Produces: tabelas `tenant_billing_settings`, `end_customer_credit_packages`, `end_customer_balances`, `end_customer_credit_transactions` (colunas exatas usadas pela Task 2); `messages.sender_type` aceita `'system'` além de `'agent'|'human'|'contact'`.

- [ ] **Step 1: Escrever a migration**

```python
"""cobrança do cliente final: settings/pacotes/saldo/ledger por tenant + sender_type system

Segunda camada de billing (cliente final -> tenant), independente do billing
tenant->plataforma já existente. Cada tenant guarda a própria secret key/webhook
secret da Stripe (cifradas) e define os próprios pacotes de crédito pros clientes.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-13
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = [
    "tenant_billing_settings",
    "end_customer_credit_packages",
    "end_customer_balances",
    "end_customer_credit_transactions",
]


def upgrade() -> None:
    op.create_table(
        "tenant_billing_settings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "billing_mode", sa.String(), server_default=sa.text("'credits'"), nullable=False
        ),
        sa.Column("stripe_secret_key_encrypted", sa.Text(), nullable=True),
        sa.Column("stripe_webhook_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("end_customer_tokens_per_credit", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_billing_settings_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_billing_settings")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_tenant_billing_settings_tenant_id")),
    )

    op.create_table(
        "end_customer_credit_packages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("price_brl", sa.Numeric(10, 2), nullable=False),
        sa.Column("credits_granted", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_end_customer_credit_packages_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_end_customer_credit_packages")),
    )
    op.create_index(
        op.f("ix_end_customer_credit_packages_tenant_id"),
        "end_customer_credit_packages",
        ["tenant_id"],
    )

    op.create_table(
        "end_customer_balances",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_phone_number", sa.String(), nullable=False),
        sa.Column("credit_balance", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_end_customer_balances_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_end_customer_balances")),
        sa.UniqueConstraint(
            "tenant_id", "contact_phone_number", name=op.f("uq_end_customer_balances_tenant_id")
        ),
    )

    op.create_table(
        "end_customer_credit_transactions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_phone_number", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("amount_credits", sa.Integer(), nullable=False),
        sa.Column("end_customer_credit_package_id", sa.Uuid(), nullable=True),
        sa.Column("related_message_id", sa.Uuid(), nullable=True),
        sa.Column("stripe_payment_id", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "type IN ('purchase', 'consumption')",
            name=op.f("ck_end_customer_credit_transactions_type"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_end_customer_credit_transactions_tenant_id_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["end_customer_credit_package_id"],
            ["end_customer_credit_packages.id"],
            name=op.f(
                "fk_end_customer_credit_transactions_end_customer_credit_package_id_end_customer_credit_packages"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["related_message_id"],
            ["messages.id"],
            name=op.f("fk_end_customer_credit_transactions_related_message_id_messages"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_end_customer_credit_transactions")),
        sa.UniqueConstraint(
            "stripe_payment_id", name=op.f("uq_end_customer_credit_transactions_stripe_payment_id")
        ),
    )
    op.create_index(
        op.f("ix_end_customer_credit_transactions_tenant_id"),
        "end_customer_credit_transactions",
        ["tenant_id"],
    )

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )

    op.drop_constraint("sender_type", "messages", type_="check")
    op.create_check_constraint(
        "sender_type", "messages", "sender_type IN ('agent', 'human', 'contact', 'system')"
    )


def downgrade() -> None:
    op.drop_constraint("sender_type", "messages", type_="check")
    op.create_check_constraint(
        "sender_type", "messages", "sender_type IN ('agent', 'human', 'contact')"
    )

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("end_customer_credit_transactions")
    op.drop_table("end_customer_balances")
    op.drop_table("end_customer_credit_packages")
    op.drop_table("tenant_billing_settings")
```

- [ ] **Step 2: Rodar a migration contra o Postgres de dev e verificar**

Run: `cd apps/api && uv run alembic upgrade head`
Expected: sem erro; `\d tenant_billing_settings` no `psql` mostra as colunas acima e `Row security: enabled`.

- [ ] **Step 3: Rodar o downgrade e o upgrade de novo (garante que a migration é reversível)**

Run: `uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: sem erro nas duas direções.

- [ ] **Step 4: Commit**

```bash
git add apps/api/alembic/versions/0009_end_customer_billing.py
git commit -m "feat(api): migration das tabelas de cobrança do cliente final"
```

---

### Task 2: Models — `TenantBillingSettings`, `EndCustomerCreditPackage`, `EndCustomerBalance`, `EndCustomerCreditTransaction`

**Files:**
- Create: `apps/api/app/models/end_customer_billing.py`
- Modify: `apps/api/app/models/__init__.py`
- Modify: `apps/api/app/models/message.py:26-28` (constraint) e `apps/api/app/schemas/conversations.py:19` (`Literal` de `MessageOut.sender_type`)

**Interfaces:**
- Consumes: tabelas da Task 1 (nomes de coluna idênticos).
- Produces: classes ORM `TenantBillingSettings`, `EndCustomerCreditPackage`, `EndCustomerBalance`, `EndCustomerCreditTransaction` (importáveis de `app.models`), usadas por todas as tasks seguintes.

- [ ] **Step 1: Criar os models**

```python
# apps/api/app/models/end_customer_billing.py
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TenantBillingSettings(Base):
    """Configuração da cobrança do cliente final (tenant-scoped, 1:1 com tenant).

    `billing_mode` só suporta "credits" por ora — hook de extensibilidade
    para modos futuros (assinatura, por conversa).
    """

    __tablename__ = "tenant_billing_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, unique=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    billing_mode: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'credits'")
    )
    stripe_secret_key_encrypted: Mapped[str | None] = mapped_column(Text)
    stripe_webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    end_customer_tokens_per_credit: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class EndCustomerCreditPackage(Base):
    """Pacote de créditos que o tenant vende aos próprios clientes finais."""

    __tablename__ = "end_customer_credit_packages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    price_brl: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    credits_granted: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class EndCustomerBalance(Base):
    """Saldo de créditos de um cliente final com um tenant específico."""

    __tablename__ = "end_customer_balances"
    __table_args__ = (
        # UniqueConstraint nomeada pela convenção do projeto (uq_%(table_name)s_%(column_0_name)s)
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    contact_phone_number: Mapped[str] = mapped_column(String, nullable=False)
    credit_balance: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class EndCustomerCreditTransaction(Base):
    """Ledger do saldo do cliente final (tenant-scoped) — purchase/consumption."""

    __tablename__ = "end_customer_credit_transactions"
    __table_args__ = (
        CheckConstraint("type IN ('purchase', 'consumption')", name="type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    contact_phone_number: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    amount_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    end_customer_credit_package_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("end_customer_credit_packages.id")
    )
    related_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("messages.id"))
    stripe_payment_id: Mapped[str | None] = mapped_column(String, unique=True)
    description: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
```

Remova o `__table_args__` vazio de `EndCustomerBalance` (deixado como comentário acima só pra explicar — a unique constraint já existe no banco via migration; não é preciso redeclará-la no model, mesmo padrão que `WhatsAppNumber` já segue pra `tenant_id` via `unique=True` na coluna. Como aqui é uma unique **composta**, use em vez disso):

```python
class EndCustomerBalance(Base):
    __tablename__ = "end_customer_balances"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    contact_phone_number: Mapped[str] = mapped_column(String, nullable=False)
    credit_balance: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
```

(a unique composta já existe no banco pela migration; o model não precisa redeclarar `__table_args__` pra isso — só a coluna basta pro ORM funcionar.)

- [ ] **Step 2: Exportar em `app/models/__init__.py`**

```python
from app.models.admin_audit_log import AdminAuditLog
from app.models.base import Base
from app.models.billing import CreditPackage, CreditTransaction
from app.models.conversation import Conversation
from app.models.end_customer_billing import (
    EndCustomerBalance,
    EndCustomerCreditPackage,
    EndCustomerCreditTransaction,
    TenantBillingSettings,
)
from app.models.knowledge_base_file import KnowledgeBaseFile
from app.models.message import Message
from app.models.platform_admin import PlatformAdmin
from app.models.tenant import Tenant
from app.models.user import User
from app.models.whatsapp_number import WhatsAppNumber

__all__ = [
    "AdminAuditLog",
    "Base",
    "CreditPackage",
    "CreditTransaction",
    "Conversation",
    "EndCustomerBalance",
    "EndCustomerCreditPackage",
    "EndCustomerCreditTransaction",
    "KnowledgeBaseFile",
    "Message",
    "PlatformAdmin",
    "Tenant",
    "TenantBillingSettings",
    "User",
    "WhatsAppNumber",
]
```

- [ ] **Step 3: Atualizar o `CheckConstraint` de `Message.sender_type`**

Em `apps/api/app/models/message.py`, troque:

```python
        CheckConstraint("sender_type IN ('agent', 'human', 'contact')", name="sender_type"),
```

por:

```python
        CheckConstraint("sender_type IN ('agent', 'human', 'contact', 'system')", name="sender_type"),
```

- [ ] **Step 4: Atualizar o `Literal` de `MessageOut.sender_type`**

Em `apps/api/app/schemas/conversations.py`, troque:

```python
    sender_type: Literal["agent", "human", "contact"]
```

por:

```python
    sender_type: Literal["agent", "human", "contact", "system"]
```

- [ ] **Step 5: Verificar que nada quebrou (sem teste dedicado pra models, mesmo padrão dos models existentes)**

Run: `uv run pytest tests/unit -q && uv run ruff check .`
Expected: todos os testes passam, sem erros de import, sem warnings do ruff.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/models/end_customer_billing.py apps/api/app/models/__init__.py apps/api/app/models/message.py apps/api/app/schemas/conversations.py
git commit -m "feat(api): models da cobrança do cliente final + sender_type system"
```

---

### Task 3: Config + criptografia da secret key/webhook secret do tenant

**Files:**
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/core/crypto.py`
- Test: `apps/api/tests/unit/test_crypto.py`

**Interfaces:**
- Produces: `settings.tenant_stripe_key_encryption_key`, `settings.internal_service_key`; `encrypt_tenant_secret(value: str) -> str`, `decrypt_tenant_secret(value: str) -> str` (usadas pela Task 5 pra cifrar a secret key **e** o webhook secret — mesma função, dois valores diferentes).

- [ ] **Step 1: Escrever o teste (roundtrip + sem chave configurada)**

Adicionar ao final de `apps/api/tests/unit/test_crypto.py`:

```python
from app.core.crypto import decrypt_tenant_secret, encrypt_tenant_secret


def test_tenant_secret_roundtrip(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tenant_stripe_key_encryption_key", Fernet.generate_key().decode())

    encrypted = encrypt_tenant_secret("sk_test_do_tenant")

    assert encrypted != "sk_test_do_tenant"
    assert decrypt_tenant_secret(encrypted) == "sk_test_do_tenant"


def test_tenant_secret_sem_chave_configurada_levanta_erro(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tenant_stripe_key_encryption_key", "")

    with pytest.raises(RuntimeError):
        encrypt_tenant_secret("sk_test")
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `uv run pytest tests/unit/test_crypto.py -v`
Expected: FAIL — `ImportError: cannot import name 'decrypt_tenant_secret'`.

- [ ] **Step 3: Adicionar a env em `app/core/config.py`**

Adicionar, junto ao bloco de Stripe existente:

```python
    # Stripe por tenant (cobrança do cliente final) — chave própria, separada
    # da usada pelo billing tenant->plataforma.
    tenant_stripe_key_encryption_key: str = ""
    # Auth de serviço interno: agents -> api (direção oposta de AGENTS_API_KEY).
    internal_service_key: str = ""
```

- [ ] **Step 4: Implementar em `app/core/crypto.py`**

Adicionar ao final do arquivo:

```python
def _tenant_fernet() -> Fernet:
    if not settings.tenant_stripe_key_encryption_key:
        raise RuntimeError("TENANT_STRIPE_KEY_ENCRYPTION_KEY não configurada")
    return Fernet(settings.tenant_stripe_key_encryption_key.encode())


def encrypt_tenant_secret(value: str) -> str:
    """Cifra a secret key OU o webhook secret da Stripe de um tenant — mesma
    chave Fernet serve pros dois valores, são independentes entre si."""
    return _tenant_fernet().encrypt(value.encode()).decode()


def decrypt_tenant_secret(value_encrypted: str) -> str:
    return _tenant_fernet().decrypt(value_encrypted.encode()).decode()
```

- [ ] **Step 5: Rodar o teste de novo**

Run: `uv run pytest tests/unit/test_crypto.py -v`
Expected: PASS (todos os testes, incluindo os já existentes).

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/core/config.py apps/api/app/core/crypto.py apps/api/tests/unit/test_crypto.py
git commit -m "feat(api): criptografia da secret key/webhook secret Stripe por tenant"
```

---

### Task 4: Auth interna (`agents` → `api`)

**Files:**
- Create: `apps/api/app/api/internal_deps.py`
- Test: `apps/api/tests/unit/test_internal_deps.py`

**Interfaces:**
- Produces: `verify_internal_service_key(authorization: str | None) -> None` (dependency usada pela Task 8 no endpoint interno de checkout — levanta `HTTPException(403)` se a key não bater).

- [ ] **Step 1: Escrever o teste**

```python
# apps/api/tests/unit/test_internal_deps.py
import pytest
from fastapi import HTTPException

from app.api.internal_deps import verify_internal_service_key
from app.core.config import settings


async def test_sem_env_configurada_nao_bloqueia(monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_service_key", "")

    await verify_internal_service_key(authorization=None)


async def test_sem_header_com_env_configurada_levanta_403(monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_service_key", "chave-secreta")

    with pytest.raises(HTTPException) as exc_info:
        await verify_internal_service_key(authorization=None)
    assert exc_info.value.status_code == 403


async def test_header_incorreto_levanta_403(monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_service_key", "chave-secreta")

    with pytest.raises(HTTPException) as exc_info:
        await verify_internal_service_key(authorization="chave-errada")
    assert exc_info.value.status_code == 403


async def test_header_correto_nao_levanta(monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_service_key", "chave-secreta")

    await verify_internal_service_key(authorization="chave-secreta")
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `uv run pytest tests/unit/test_internal_deps.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.internal_deps'`.

- [ ] **Step 3: Implementar**

```python
# apps/api/app/api/internal_deps.py
"""Auth de serviço interno: agents -> api (direção oposta de AGENTS_API_KEY,
que autentica o api/worker chamando o agents)."""

import secrets

from fastapi import Header, HTTPException, status

from app.core.config import settings


async def verify_internal_service_key(authorization: str | None = Header(default=None)) -> None:
    if not settings.internal_service_key:
        return
    if not authorization or not secrets.compare_digest(
        authorization, settings.internal_service_key
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Key inválida ou ausente",
        )
```

- [ ] **Step 4: Rodar de novo**

Run: `uv run pytest tests/unit/test_internal_deps.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/api/internal_deps.py apps/api/tests/unit/test_internal_deps.py
git commit -m "feat(api): auth de serviço interno para o agents chamar o api"
```

---

### Task 5: Configuração da cobrança (`GET`/`PATCH /api/v1/end-customer-billing/settings`)

**Files:**
- Create: `apps/api/app/schemas/end_customer_billing.py`
- Create: `apps/api/app/api/v1/end_customer_billing.py`
- Modify: `apps/api/app/api/v1/router.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_settings_routes.py`

**Interfaces:**
- Consumes: `TenantBillingSettings` (Task 2), `encrypt_tenant_secret` (Task 3), `get_current_tenant`/`get_tenant_session` (`app/api/deps.py`, já existente).
- Produces: `TenantBillingSettingsOut`, `TenantBillingSettingsUpdate` (schemas reaproveitados pela Task 6 no mesmo arquivo); rotas montadas em `/api/v1/end-customer-billing/settings`.

- [ ] **Step 1: Escrever os schemas**

```python
# apps/api/app/schemas/end_customer_billing.py
from pydantic import BaseModel, Field


class TenantBillingSettingsOut(BaseModel):
    enabled: bool
    billing_mode: str
    stripe_secret_key_configured: bool
    stripe_webhook_secret_configured: bool
    end_customer_tokens_per_credit: int | None


class TenantBillingSettingsUpdate(BaseModel):
    """PATCH parcial — campos omitidos mantêm o valor já salvo.

    `stripe_secret_key`/`stripe_webhook_secret` omitidos não sobrescrevem o
    valor cifrado existente (evita ter que reenviar a secret key a cada PATCH
    de outro campo, ex: só ligar o toggle `enabled`).
    """

    enabled: bool | None = None
    stripe_secret_key: str | None = Field(default=None, min_length=1)
    stripe_webhook_secret: str | None = Field(default=None, min_length=1)
    end_customer_tokens_per_credit: int | None = Field(default=None, gt=0)
```

- [ ] **Step 2: Escrever o teste das rotas de settings**

```python
# apps/api/tests/unit/test_end_customer_billing_settings_routes.py
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app

TENANT_ID = uuid.uuid4()


def _settings_row(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        enabled=False,
        billing_mode="credits",
        stripe_secret_key_encrypted=None,
        stripe_webhook_secret_encrypted=None,
        end_customer_tokens_per_credit=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


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


def test_get_sem_configuracao_retorna_default(client, session) -> None:
    session.scalar.return_value = None

    response = client.get("/api/v1/end-customer-billing/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["stripe_secret_key_configured"] is False
    assert body["stripe_webhook_secret_configured"] is False


def test_get_com_configuracao_nao_revela_secrets(client, session) -> None:
    session.scalar.return_value = _settings_row(
        stripe_secret_key_encrypted="cifrado", stripe_webhook_secret_encrypted="cifrado-2"
    )

    response = client.get("/api/v1/end-customer-billing/settings")

    body = response.json()
    assert body["stripe_secret_key_configured"] is True
    assert "stripe_secret_key_encrypted" not in body
    assert "stripe_secret_key" not in body


def test_patch_sem_secret_key_e_sem_habilitar_nao_exige_nada(client, session) -> None:
    session.scalar.return_value = None

    response = client.patch(
        "/api/v1/end-customer-billing/settings", json={"end_customer_tokens_per_credit": 500}
    )

    assert response.status_code == 200
    session.commit.assert_awaited_once()


def test_patch_habilitar_sem_secret_key_configurada_retorna_400(client, session) -> None:
    session.scalar.return_value = None

    response = client.patch("/api/v1/end-customer-billing/settings", json={"enabled": True})

    assert response.status_code == 400


def test_patch_habilitar_sem_tokens_per_credit_configurado_retorna_400(client, session) -> None:
    session.scalar.return_value = _settings_row(stripe_secret_key_encrypted="cifrado")

    response = client.patch("/api/v1/end-customer-billing/settings", json={"enabled": True})

    assert response.status_code == 400


def test_patch_habilitar_com_tudo_configurado_funciona(client, session) -> None:
    session.scalar.return_value = _settings_row(
        stripe_secret_key_encrypted="cifrado", end_customer_tokens_per_credit=500
    )

    response = client.patch("/api/v1/end-customer-billing/settings", json={"enabled": True})

    assert response.status_code == 200
    assert response.json()["enabled"] is True


def test_patch_cria_registro_quando_nao_existe(client, session, monkeypatch) -> None:
    session.scalar.return_value = None
    added = []
    session.add = MagicMock(side_effect=lambda obj: added.append(obj))
    monkeypatch.setattr(
        "app.api.v1.end_customer_billing.encrypt_tenant_secret", lambda v: f"cifrado:{v}"
    )

    response = client.patch(
        "/api/v1/end-customer-billing/settings",
        json={"stripe_secret_key": "sk_test_123", "end_customer_tokens_per_credit": 300},
    )

    assert response.status_code == 200
    assert len(added) == 1
    created = added[0]
    assert created.tenant_id == TENANT_ID
    assert created.stripe_secret_key_encrypted == "cifrado:sk_test_123"
    assert created.end_customer_tokens_per_credit == 300


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/end-customer-billing/settings")
    assert response.status_code == 401
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `uv run pytest tests/unit/test_end_customer_billing_settings_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.v1.end_customer_billing'`.

- [ ] **Step 4: Implementar a rota**

```python
# apps/api/app/api/v1/end_customer_billing.py
"""Configuração da cobrança do cliente final (Stripe própria do tenant) e
pacotes de crédito que o tenant vende aos próprios clientes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.core.crypto import encrypt_tenant_secret
from app.models import TenantBillingSettings
from app.schemas.end_customer_billing import TenantBillingSettingsOut, TenantBillingSettingsUpdate

router = APIRouter(prefix="/end-customer-billing", tags=["end-customer-billing"])


def _to_settings_out(settings_row: TenantBillingSettings | None) -> TenantBillingSettingsOut:
    if settings_row is None:
        return TenantBillingSettingsOut(
            enabled=False,
            billing_mode="credits",
            stripe_secret_key_configured=False,
            stripe_webhook_secret_configured=False,
            end_customer_tokens_per_credit=None,
        )
    return TenantBillingSettingsOut(
        enabled=settings_row.enabled,
        billing_mode=settings_row.billing_mode,
        stripe_secret_key_configured=settings_row.stripe_secret_key_encrypted is not None,
        stripe_webhook_secret_configured=settings_row.stripe_webhook_secret_encrypted is not None,
        end_customer_tokens_per_credit=settings_row.end_customer_tokens_per_credit,
    )


async def _get_settings_row(
    ctx: TenantContext, session: AsyncSession
) -> TenantBillingSettings | None:
    return await session.scalar(
        select(TenantBillingSettings).where(TenantBillingSettings.tenant_id == ctx.tenant_id)
    )


@router.get("/settings")
async def get_settings(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> TenantBillingSettingsOut:
    return _to_settings_out(await _get_settings_row(ctx, session))


@router.patch("/settings")
async def update_settings(
    body: TenantBillingSettingsUpdate,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> TenantBillingSettingsOut:
    row = await _get_settings_row(ctx, session)
    if row is None:
        row = TenantBillingSettings(tenant_id=ctx.tenant_id)
        session.add(row)

    if body.stripe_secret_key is not None:
        row.stripe_secret_key_encrypted = encrypt_tenant_secret(body.stripe_secret_key)
    if body.stripe_webhook_secret is not None:
        row.stripe_webhook_secret_encrypted = encrypt_tenant_secret(body.stripe_webhook_secret)
    if body.end_customer_tokens_per_credit is not None:
        row.end_customer_tokens_per_credit = body.end_customer_tokens_per_credit

    if body.enabled is True:
        if row.stripe_secret_key_encrypted is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Configure a secret key da Stripe antes de ativar a cobrança",
            )
        if not row.end_customer_tokens_per_credit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Configure a conversão de tokens por crédito antes de ativar a cobrança",
            )
    if body.enabled is not None:
        row.enabled = body.enabled

    await session.commit()
    return _to_settings_out(row)
```

- [ ] **Step 5: Registrar o router**

Em `apps/api/app/api/v1/router.py`, adicionar o import e o include (ordem alfabética, mesmo padrão dos demais):

```python
from app.api.v1.end_customer_billing import router as end_customer_billing_router
```

```python
api_router.include_router(end_customer_billing_router)
```

- [ ] **Step 6: Rodar o teste de novo**

Run: `uv run pytest tests/unit/test_end_customer_billing_settings_routes.py -v`
Expected: PASS (todos os 8 testes).

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/schemas/end_customer_billing.py apps/api/app/api/v1/end_customer_billing.py apps/api/app/api/v1/router.py apps/api/tests/unit/test_end_customer_billing_settings_routes.py
git commit -m "feat(api): configuração da cobrança do cliente final por tenant"
```

---

### Task 6: Pacotes de crédito do cliente final (CRUD)

**Files:**
- Modify: `apps/api/app/schemas/end_customer_billing.py`
- Modify: `apps/api/app/api/v1/end_customer_billing.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_packages_routes.py`

**Interfaces:**
- Consumes: `EndCustomerCreditPackage`, `EndCustomerCreditTransaction` (Task 2).
- Produces: `EndCustomerCreditPackageOut`/`In`/`Update`; rotas `GET/POST /end-customer-billing/packages`, `PATCH/DELETE /end-customer-billing/packages/{id}` — usadas pela Task 7 (checkout) e pelo Plano 2 (`worker` monta a lista de pacotes ativos direto do banco, mas o `web` consome estas rotas).

- [ ] **Step 1: Adicionar os schemas de pacote**

Adicionar em `apps/api/app/schemas/end_customer_billing.py`:

```python
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class EndCustomerCreditPackageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    price_brl: Decimal
    credits_granted: int
    active: bool


class EndCustomerCreditPackageIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    price_brl: Decimal = Field(gt=0)
    credits_granted: int = Field(gt=0)
    active: bool = True


class EndCustomerCreditPackageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price_brl: Decimal | None = Field(default=None, gt=0)
    credits_granted: int | None = Field(default=None, gt=0)
    active: bool | None = None
```

(mover o `import uuid`/`Decimal`/`Field` acrescentados pro topo do arquivo, junto dos imports já existentes de `TenantBillingSettingsOut`/`Update` — um único bloco de imports no arquivo, sem duplicar.)

- [ ] **Step 2: Escrever o teste das rotas de pacotes**

```python
# apps/api/tests/unit/test_end_customer_billing_packages_routes.py
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app

TENANT_ID = uuid.uuid4()
PACKAGE_ID = uuid.uuid4()


def _package(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        id=PACKAGE_ID,
        tenant_id=TENANT_ID,
        name="Pacote Básico",
        price_brl=Decimal("49.90"),
        credits_granted=500,
        active=True,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


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


def test_list_retorna_pacotes_do_tenant(client, session) -> None:
    result = MagicMock()
    result.scalars.return_value.all.return_value = [_package()]
    session.execute.return_value = result

    response = client.get("/api/v1/end-customer-billing/packages")

    assert response.status_code == 200
    assert response.json()[0]["name"] == "Pacote Básico"


def test_create_persiste_pacote(client, session) -> None:
    added = []
    session.add = MagicMock(side_effect=lambda obj: added.append(obj))

    async def fake_refresh(obj):
        obj.id = PACKAGE_ID

    session.refresh.side_effect = fake_refresh

    response = client.post(
        "/api/v1/end-customer-billing/packages",
        json={"name": "Growth", "price_brl": "99.90", "credits_granted": 1000},
    )

    assert response.status_code == 201
    assert len(added) == 1
    assert added[0].tenant_id == TENANT_ID
    assert added[0].name == "Growth"


def test_update_pacote_inexistente_retorna_404(client, session) -> None:
    session.scalar.return_value = None

    response = client.patch(f"/api/v1/end-customer-billing/packages/{PACKAGE_ID}", json={"active": False})

    assert response.status_code == 404


def test_update_pacote_desativa(client, session) -> None:
    session.scalar.return_value = _package()

    response = client.patch(f"/api/v1/end-customer-billing/packages/{PACKAGE_ID}", json={"active": False})

    assert response.status_code == 200
    assert response.json()["active"] is False


def test_delete_pacote_ja_usado_retorna_409(client, session) -> None:
    session.scalar = AsyncMock(side_effect=[_package(), uuid.uuid4()])

    response = client.delete(f"/api/v1/end-customer-billing/packages/{PACKAGE_ID}")

    assert response.status_code == 409


def test_delete_pacote_nao_usado_remove(client, session) -> None:
    package = _package()
    session.scalar = AsyncMock(side_effect=[package, None])
    session.delete = AsyncMock()

    response = client.delete(f"/api/v1/end-customer-billing/packages/{PACKAGE_ID}")

    assert response.status_code == 204
    session.delete.assert_awaited_once_with(package)
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `uv run pytest tests/unit/test_end_customer_billing_packages_routes.py -v`
Expected: FAIL — `404` genérico (rotas não existem ainda, o roteador global as devolve como 404 do FastAPI) em vez dos status esperados.

- [ ] **Step 4: Implementar as rotas de pacote**

Adicionar em `apps/api/app/api/v1/end_customer_billing.py` (mesmos imports de `select`/`status`/`HTTPException` já presentes; adicionar `uuid` e os models/schemas novos ao topo):

```python
import uuid

from app.models import EndCustomerCreditPackage, EndCustomerCreditTransaction
from app.schemas.end_customer_billing import (
    EndCustomerCreditPackageIn,
    EndCustomerCreditPackageOut,
    EndCustomerCreditPackageUpdate,
)
```

```python
@router.get("/packages")
async def list_packages(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[EndCustomerCreditPackageOut]:
    result = await session.execute(
        select(EndCustomerCreditPackage).where(
            EndCustomerCreditPackage.tenant_id == ctx.tenant_id
        )
    )
    return [EndCustomerCreditPackageOut.model_validate(p) for p in result.scalars().all()]


@router.post("/packages", status_code=status.HTTP_201_CREATED)
async def create_package(
    body: EndCustomerCreditPackageIn,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> EndCustomerCreditPackageOut:
    package = EndCustomerCreditPackage(tenant_id=ctx.tenant_id, **body.model_dump())
    session.add(package)
    await session.commit()
    await session.refresh(package)
    return EndCustomerCreditPackageOut.model_validate(package)


async def _get_package(
    package_id: uuid.UUID, ctx: TenantContext, session: AsyncSession
) -> EndCustomerCreditPackage:
    package = await session.scalar(
        select(EndCustomerCreditPackage).where(
            EndCustomerCreditPackage.id == package_id,
            EndCustomerCreditPackage.tenant_id == ctx.tenant_id,
        )
    )
    if package is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pacote não encontrado")
    return package


@router.patch("/packages/{package_id}")
async def update_package(
    package_id: uuid.UUID,
    body: EndCustomerCreditPackageUpdate,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> EndCustomerCreditPackageOut:
    package = await _get_package(package_id, ctx, session)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(package, field, value)
    await session.commit()
    await session.refresh(package)
    return EndCustomerCreditPackageOut.model_validate(package)


@router.delete("/packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_package(
    package_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    package = await _get_package(package_id, ctx, session)
    used = await session.scalar(
        select(EndCustomerCreditTransaction.id).where(
            EndCustomerCreditTransaction.end_customer_credit_package_id == package_id
        )
    )
    if used is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pacote já usado em compras — desative em vez de excluir",
        )
    await session.delete(package)
    await session.commit()
```

- [ ] **Step 5: Rodar o teste de novo**

Run: `uv run pytest tests/unit/test_end_customer_billing_packages_routes.py -v`
Expected: PASS (todos os 6 testes).

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/schemas/end_customer_billing.py apps/api/app/api/v1/end_customer_billing.py apps/api/tests/unit/test_end_customer_billing_packages_routes.py
git commit -m "feat(api): CRUD de pacotes de crédito do cliente final"
```

---

### Task 7: Serviço — criação de Checkout Session dinâmica

**Files:**
- Create: `apps/api/app/services/end_customer_billing.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_service.py`

**Interfaces:**
- Consumes: `TenantBillingSettings`, `EndCustomerCreditPackage` (Task 2); `decrypt_tenant_secret` (Task 3).
- Produces: `create_end_customer_checkout_session(session, tenant_id, contact_phone_number, package_id) -> str`; exceções `BillingNotConfiguredError`, `InvalidPackageError`, `StripeApiError` — consumidas pela Task 8.

- [ ] **Step 1: Escrever os testes**

```python
# apps/api/tests/unit/test_end_customer_billing_service.py
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.end_customer_billing as service
from app.services.end_customer_billing import (
    BillingNotConfiguredError,
    InvalidPackageError,
    StripeApiError,
    create_end_customer_checkout_session,
)

TENANT_ID = uuid.uuid4()
PACKAGE_ID = uuid.uuid4()
CONTACT = "5511999998888"


def _settings_row(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        enabled=True,
        stripe_secret_key_encrypted="cifrado",
        stripe_webhook_secret_encrypted="cifrado-webhook",
        end_customer_tokens_per_credit=500,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _package(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        id=PACKAGE_ID, tenant_id=TENANT_ID, name="Básico", price_brl=Decimal("49.90"),
        credits_granted=500, active=True,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


@pytest.fixture
def session():
    return AsyncMock()


class TestCreateEndCustomerCheckoutSession:
    async def test_sem_settings_levanta_erro(self, session) -> None:
        session.scalar = AsyncMock(return_value=None)

        with pytest.raises(BillingNotConfiguredError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

    async def test_settings_desabilitado_levanta_erro(self, session) -> None:
        session.scalar = AsyncMock(return_value=_settings_row(enabled=False))

        with pytest.raises(BillingNotConfiguredError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

    async def test_pacote_inexistente_levanta_erro(self, session) -> None:
        session.scalar = AsyncMock(side_effect=[_settings_row(), None])

        with pytest.raises(InvalidPackageError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

    async def test_pacote_inativo_levanta_erro(self, session) -> None:
        session.scalar = AsyncMock(side_effect=[_settings_row(), _package(active=False)])

        with pytest.raises(InvalidPackageError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

    async def test_sucesso_usa_secret_key_do_tenant_e_metadata_correta(
        self, session, monkeypatch
    ) -> None:
        session.scalar = AsyncMock(side_effect=[_settings_row(), _package()])
        monkeypatch.setattr(service, "decrypt_tenant_secret", lambda v: "sk_test_do_tenant")
        created = MagicMock(
            return_value=SimpleNamespace(url="https://checkout.stripe.com/pay/cs_end_1")
        )
        monkeypatch.setattr(service.stripe.checkout.Session, "create", created)

        url = await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

        assert url == "https://checkout.stripe.com/pay/cs_end_1"
        kwargs = created.call_args.kwargs
        assert kwargs["api_key"] == "sk_test_do_tenant"
        assert kwargs["mode"] == "payment"
        assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 4990
        assert kwargs["metadata"] == {
            "tenant_id": str(TENANT_ID),
            "contact_phone_number": CONTACT,
            "package_id": str(PACKAGE_ID),
            "kind": "end_customer_purchase",
        }

    async def test_falha_na_stripe_levanta_stripe_api_error(self, session, monkeypatch) -> None:
        session.scalar = AsyncMock(side_effect=[_settings_row(), _package()])
        monkeypatch.setattr(service, "decrypt_tenant_secret", lambda v: "sk_test_do_tenant")

        def _raise(*args, **kwargs):
            raise service.stripe.error.StripeError("falhou")

        monkeypatch.setattr(service.stripe.checkout.Session, "create", _raise)

        with pytest.raises(StripeApiError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `uv run pytest tests/unit/test_end_customer_billing_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.end_customer_billing'`.

- [ ] **Step 3: Implementar**

```python
# apps/api/app/services/end_customer_billing.py
"""Cobrança do cliente final: cada tenant usa a própria conta Stripe pra
vender créditos aos próprios clientes. Espelha app/services/billing.py
(billing tenant->plataforma), mas com a secret key sendo a do TENANT, nunca
a global — por isso toda chamada à Stripe aqui passa api_key= explicitamente,
nunca via stripe.api_key global (que vazaria entre tenants concorrentes).
"""

import logging
import uuid

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_tenant_secret
from app.models import EndCustomerCreditPackage, TenantBillingSettings

logger = logging.getLogger(__name__)


class BillingNotConfiguredError(Exception):
    """Tenant sem cobrança habilitada ou sem secret key configurada."""


class InvalidPackageError(Exception):
    """Pacote inexistente, de outro tenant, ou inativo."""


class StripeApiError(Exception):
    """Falha ao criar a sessão de checkout na Stripe (rede ou resposta de erro)."""


async def create_end_customer_checkout_session(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    contact_phone_number: str,
    package_id: uuid.UUID,
) -> str:
    billing_settings = await session.scalar(
        select(TenantBillingSettings).where(TenantBillingSettings.tenant_id == tenant_id)
    )
    if (
        billing_settings is None
        or not billing_settings.enabled
        or billing_settings.stripe_secret_key_encrypted is None
    ):
        raise BillingNotConfiguredError("Cobrança do cliente final não configurada pelo tenant")

    package = await session.scalar(
        select(EndCustomerCreditPackage).where(
            EndCustomerCreditPackage.id == package_id,
            EndCustomerCreditPackage.tenant_id == tenant_id,
        )
    )
    if package is None or not package.active:
        raise InvalidPackageError("Pacote de créditos inválido")

    secret_key = decrypt_tenant_secret(billing_settings.stripe_secret_key_encrypted)

    try:
        checkout_session = stripe.checkout.Session.create(
            api_key=secret_key,
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": "brl",
                        "unit_amount": int(package.price_brl * 100),
                        "product_data": {"name": package.name},
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "tenant_id": str(tenant_id),
                "contact_phone_number": contact_phone_number,
                "package_id": str(package_id),
                "kind": "end_customer_purchase",
            },
            success_url=f"{settings.web_app_url}/pagamento-confirmado",
            cancel_url=f"{settings.web_app_url}/pagamento-confirmado",
        )
    except stripe.error.StripeError as exc:
        logger.error("Falha ao criar checkout do cliente final | erro=%s", exc)
        raise StripeApiError("Falha ao iniciar o pagamento — tente novamente em instantes") from exc

    return checkout_session.url
```

- [ ] **Step 4: Rodar o teste de novo**

Run: `uv run pytest tests/unit/test_end_customer_billing_service.py -v`
Expected: PASS (todos os 6 testes).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/end_customer_billing.py apps/api/tests/unit/test_end_customer_billing_service.py
git commit -m "feat(api): criação de checkout dinâmico com a secret key do tenant"
```

---

### Task 8: Endpoint interno de checkout (`agents` → `api`)

**Files:**
- Create: `apps/api/app/api/v1/internal/__init__.py`
- Create: `apps/api/app/api/v1/internal/end_customer_billing.py`
- Modify: `apps/api/app/api/v1/router.py`
- Modify: `apps/api/app/schemas/end_customer_billing.py`
- Test: `apps/api/tests/unit/test_internal_end_customer_billing_routes.py`

**Interfaces:**
- Consumes: `create_end_customer_checkout_session` (Task 7), `verify_internal_service_key` (Task 4).
- Produces: `POST /api/v1/internal/end-customer-billing/checkout` — contrato `{tenant_id, contact_phone_number, package_id} -> {checkout_url}`, é o que a tool `gerar_link_pagamento_cliente` do `agents` vai chamar no Plano 2.

- [ ] **Step 1: Adicionar os schemas do endpoint interno**

Adicionar em `apps/api/app/schemas/end_customer_billing.py`:

```python
class InternalCheckoutRequest(BaseModel):
    tenant_id: uuid.UUID
    contact_phone_number: str = Field(min_length=1)
    package_id: uuid.UUID


class CheckoutUrlOut(BaseModel):
    checkout_url: str
```

- [ ] **Step 2: Escrever o teste**

```python
# apps/api/tests/unit/test_internal_end_customer_billing_routes.py
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.internal.end_customer_billing as internal_module
from app.core.config import settings
from app.core.db import get_system_session
from app.main import app
from app.services.end_customer_billing import (
    BillingNotConfiguredError,
    InvalidPackageError,
    StripeApiError,
)

TENANT_ID = str(uuid.uuid4())
PACKAGE_ID = str(uuid.uuid4())
PAYLOAD = {
    "tenant_id": TENANT_ID,
    "contact_phone_number": "5511999998888",
    "package_id": PACKAGE_ID,
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_key", "")

    async def override_session():
        yield AsyncMock()

    app.dependency_overrides[get_system_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_sem_api_key_com_env_configurada_retorna_403(monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_service_key", "chave-interna")

    response = TestClient(app).post("/api/v1/internal/end-customer-billing/checkout", json=PAYLOAD)

    assert response.status_code == 403


def test_sucesso_retorna_checkout_url(client, monkeypatch) -> None:
    monkeypatch.setattr(
        internal_module,
        "create_end_customer_checkout_session",
        AsyncMock(return_value="https://checkout.stripe.com/pay/cs_1"),
    )

    response = client.post("/api/v1/internal/end-customer-billing/checkout", json=PAYLOAD)

    assert response.status_code == 200
    assert response.json() == {"checkout_url": "https://checkout.stripe.com/pay/cs_1"}


def test_billing_nao_configurado_retorna_404(client, monkeypatch) -> None:
    monkeypatch.setattr(
        internal_module,
        "create_end_customer_checkout_session",
        AsyncMock(side_effect=BillingNotConfiguredError("não configurado")),
    )

    response = client.post("/api/v1/internal/end-customer-billing/checkout", json=PAYLOAD)

    assert response.status_code == 404


def test_pacote_invalido_retorna_400(client, monkeypatch) -> None:
    monkeypatch.setattr(
        internal_module,
        "create_end_customer_checkout_session",
        AsyncMock(side_effect=InvalidPackageError("inválido")),
    )

    response = client.post("/api/v1/internal/end-customer-billing/checkout", json=PAYLOAD)

    assert response.status_code == 400


def test_falha_na_stripe_retorna_502(client, monkeypatch) -> None:
    monkeypatch.setattr(
        internal_module,
        "create_end_customer_checkout_session",
        AsyncMock(side_effect=StripeApiError("falhou")),
    )

    response = client.post("/api/v1/internal/end-customer-billing/checkout", json=PAYLOAD)

    assert response.status_code == 502
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `uv run pytest tests/unit/test_internal_end_customer_billing_routes.py -v`
Expected: FAIL — `404` (rota inexistente) nos casos que esperam outros status.

- [ ] **Step 4: Implementar**

```python
# apps/api/app/api/v1/internal/__init__.py
```

```python
# apps/api/app/api/v1/internal/end_customer_billing.py
"""Endpoint interno chamado pelo agents (nunca pelo escritório/cliente
final diretamente) — cria o Checkout Session sem expor a secret key do
tenant ao serviço de agentes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.internal_deps import verify_internal_service_key
from app.core.db import get_system_session
from app.schemas.end_customer_billing import CheckoutUrlOut, InternalCheckoutRequest
from app.services.end_customer_billing import (
    BillingNotConfiguredError,
    InvalidPackageError,
    StripeApiError,
    create_end_customer_checkout_session,
)

router = APIRouter(
    prefix="/internal/end-customer-billing",
    tags=["internal"],
    dependencies=[Depends(verify_internal_service_key)],
)


@router.post("/checkout")
async def create_checkout(
    body: InternalCheckoutRequest,
    session: AsyncSession = Depends(get_system_session),
) -> CheckoutUrlOut:
    try:
        checkout_url = await create_end_customer_checkout_session(
            session, body.tenant_id, body.contact_phone_number, body.package_id
        )
    except BillingNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidPackageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except StripeApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return CheckoutUrlOut(checkout_url=checkout_url)
```

- [ ] **Step 5: Registrar o router**

Em `apps/api/app/api/v1/router.py`:

```python
from app.api.v1.internal.end_customer_billing import router as internal_end_customer_billing_router
```

```python
api_router.include_router(internal_end_customer_billing_router)
```

- [ ] **Step 6: Rodar o teste de novo**

Run: `uv run pytest tests/unit/test_internal_end_customer_billing_routes.py -v`
Expected: PASS (todos os 5 testes).

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/api/v1/internal/ apps/api/app/api/v1/router.py apps/api/app/schemas/end_customer_billing.py apps/api/tests/unit/test_internal_end_customer_billing_routes.py
git commit -m "feat(api): endpoint interno de checkout pro agents chamar"
```

---

### Task 9: Serviço — processamento do webhook + confirmação ao cliente final

**Files:**
- Modify: `apps/api/app/services/end_customer_billing.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_service.py`

**Interfaces:**
- Consumes: `EndCustomerBalance`, `EndCustomerCreditTransaction` (Task 2); `WhatsAppNumber`, `Conversation`, `Message` (models existentes); `send_text_message`, `decrypt_access_token` (clients/crypto existentes).
- Produces: `process_end_customer_checkout_completed(session, tenant_id, stripe_session: dict) -> None` — consumida pela Task 10 (rota de webhook).

- [ ] **Step 1: Escrever os testes**

Adicionar ao final de `apps/api/tests/unit/test_end_customer_billing_service.py`:

```python
from datetime import UTC, datetime

from app.services.end_customer_billing import process_end_customer_checkout_completed


def _conversation(**overrides):
    row = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=TENANT_ID, contact_phone_number=CONTACT,
        last_message_at=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _number(**overrides):
    row = SimpleNamespace(
        tenant_id=TENANT_ID, phone_number_id="PNID", access_token_encrypted="cifrado",
        status="connected",
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _checkout_session(**metadata_overrides) -> dict:
    metadata = {
        "tenant_id": str(TENANT_ID),
        "contact_phone_number": CONTACT,
        "package_id": str(PACKAGE_ID),
        "kind": "end_customer_purchase",
    }
    metadata.update(metadata_overrides)
    return {"id": "cs_end_999", "metadata": metadata}


class TestProcessEndCustomerCheckoutCompleted:
    async def test_ja_processado_nao_faz_nada(self, session) -> None:
        session.scalar = AsyncMock(return_value=uuid.uuid4())

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        session.add.assert_not_called()

    async def test_metadata_sem_kind_correto_e_ignorada(self, session) -> None:
        session.scalar = AsyncMock(return_value=None)

        await process_end_customer_checkout_completed(
            session, TENANT_ID, _checkout_session(kind="outra_coisa")
        )

        session.add.assert_not_called()

    async def test_pacote_nao_encontrado_nao_processa(self, session) -> None:
        session.scalar = AsyncMock(side_effect=[None, None])

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        session.add.assert_not_called()

    async def test_credita_saldo_novo_e_manda_confirmacao(self, session, monkeypatch) -> None:
        package = _package()
        conversation = _conversation()
        number = _number()
        session.scalar = AsyncMock(
            side_effect=[None, package, None, conversation, number]
        )
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        session.flush = AsyncMock()
        send = AsyncMock()
        monkeypatch.setattr(service, "send_text_message", send)
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        balance, transaction, message = added
        assert balance.credit_balance == package.credits_granted
        assert transaction.type == "purchase"
        assert transaction.amount_credits == package.credits_granted
        assert transaction.stripe_payment_id == "cs_end_999"
        assert message.sender_type == "system"
        send.assert_awaited_once()
        assert send.await_args.kwargs["to"] == CONTACT
        session.commit.assert_awaited()

    async def test_credita_saldo_existente_soma(self, session, monkeypatch) -> None:
        package = _package()
        existing_balance = SimpleNamespace(
            tenant_id=TENANT_ID, contact_phone_number=CONTACT, credit_balance=100,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.scalar = AsyncMock(side_effect=[None, package, existing_balance, None, None])
        session.add = MagicMock()
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        assert existing_balance.credit_balance == 100 + package.credits_granted

    async def test_falha_ao_confirmar_via_whatsapp_nao_impede_credito(
        self, session, monkeypatch
    ) -> None:
        package = _package()
        session.scalar = AsyncMock(side_effect=[None, package, None, None, None])
        session.add = MagicMock()
        monkeypatch.setattr(
            service, "send_text_message", AsyncMock(side_effect=RuntimeError("falhou"))
        )

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        session.commit.assert_awaited()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `uv run pytest tests/unit/test_end_customer_billing_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'process_end_customer_checkout_completed'`.

- [ ] **Step 3: Implementar**

Adicionar aos imports do topo de `apps/api/app/services/end_customer_billing.py`:

```python
from datetime import UTC, datetime

from app.clients.whatsapp import WhatsAppSendError, send_text_message
from app.core.crypto import decrypt_access_token
from app.models import Conversation, EndCustomerBalance, EndCustomerCreditTransaction, Message, WhatsAppNumber
```

Adicionar ao final do arquivo:

```python
async def process_end_customer_checkout_completed(
    session: AsyncSession, tenant_id: uuid.UUID, stripe_session: dict
) -> None:
    """Credita o pacote comprado pelo cliente final e confirma via WhatsApp.

    Idempotente por stripe_payment_id, mesmo padrão do billing tenant->plataforma.
    """
    session_id = stripe_session["id"]
    already_processed = await session.scalar(
        select(EndCustomerCreditTransaction.id).where(
            EndCustomerCreditTransaction.stripe_payment_id == session_id
        )
    )
    if already_processed is not None:
        logger.info("Webhook de cliente final duplicado, ignorando | session=%s", session_id)
        return

    raw_metadata = stripe_session["metadata"] if "metadata" in stripe_session else {}
    metadata = raw_metadata.to_dict() if hasattr(raw_metadata, "to_dict") else dict(raw_metadata)

    if metadata.get("kind") != "end_customer_purchase":
        return

    contact_phone_number = metadata.get("contact_phone_number")
    package_id_raw = metadata.get("package_id")
    if not contact_phone_number or not package_id_raw:
        logger.error("Metadata incompleta no webhook de cliente final | session=%s", session_id)
        return

    package = await session.scalar(
        select(EndCustomerCreditPackage).where(
            EndCustomerCreditPackage.id == uuid.UUID(package_id_raw),
            EndCustomerCreditPackage.tenant_id == tenant_id,
        )
    )
    if package is None:
        logger.error("Pacote não encontrado no webhook de cliente final | session=%s", session_id)
        return

    balance = await session.scalar(
        select(EndCustomerBalance).where(
            EndCustomerBalance.tenant_id == tenant_id,
            EndCustomerBalance.contact_phone_number == contact_phone_number,
        )
    )
    if balance is None:
        balance = EndCustomerBalance(
            tenant_id=tenant_id, contact_phone_number=contact_phone_number, credit_balance=0
        )
        session.add(balance)
        await session.flush()

    balance.credit_balance += package.credits_granted
    balance.updated_at = datetime.now(UTC)

    session.add(
        EndCustomerCreditTransaction(
            tenant_id=tenant_id,
            contact_phone_number=contact_phone_number,
            type="purchase",
            amount_credits=package.credits_granted,
            end_customer_credit_package_id=package.id,
            stripe_payment_id=session_id,
            description=f"Compra do pacote {package.name}",
        )
    )
    await session.commit()

    await _send_purchase_confirmation(session, tenant_id, contact_phone_number)


async def _send_purchase_confirmation(
    session: AsyncSession, tenant_id: uuid.UUID, contact_phone_number: str
) -> None:
    """Best-effort: uma falha ao mandar a confirmação não desfaz o crédito
    já commitado acima — o cliente só não recebe o aviso, mas o saldo está lá."""
    try:
        number = await session.scalar(
            select(WhatsAppNumber).where(
                WhatsAppNumber.tenant_id == tenant_id, WhatsAppNumber.status == "connected"
            )
        )
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.contact_phone_number == contact_phone_number,
            )
        )
        if number is None or conversation is None:
            logger.warning(
                "Sem número/conversa pra confirmar pagamento | tenant=%s contato=%s",
                tenant_id,
                contact_phone_number,
            )
            return

        await send_text_message(
            phone_number_id=number.phone_number_id,
            access_token=decrypt_access_token(number.access_token_encrypted),
            to=contact_phone_number,
            text="Pagamento confirmado! Você já pode continuar a conversa.",
        )

        session.add(
            Message(
                conversation_id=conversation.id,
                tenant_id=tenant_id,
                sender_type="system",
                content="Pagamento confirmado! Você já pode continuar a conversa.",
                delivery_status="sent",
            )
        )
        conversation.last_message_at = datetime.now(UTC)
        await session.commit()
    except WhatsAppSendError:
        logger.exception(
            "Falha ao confirmar pagamento via WhatsApp | tenant=%s contato=%s",
            tenant_id,
            contact_phone_number,
        )
    except Exception:
        logger.exception(
            "Erro inesperado ao confirmar pagamento | tenant=%s contato=%s",
            tenant_id,
            contact_phone_number,
        )
```

- [ ] **Step 4: Rodar o teste de novo**

Run: `uv run pytest tests/unit/test_end_customer_billing_service.py -v`
Expected: PASS (todos os testes da classe `TestProcessEndCustomerCheckoutCompleted` + os anteriores da Task 7).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/end_customer_billing.py apps/api/tests/unit/test_end_customer_billing_service.py
git commit -m "feat(api): processamento do webhook de compra do cliente final"
```

---

### Task 10: Rota de webhook por tenant

**Files:**
- Create: `apps/api/app/api/v1/webhooks/stripe_tenant.py`
- Modify: `apps/api/app/api/v1/router.py`
- Test: `apps/api/tests/unit/test_stripe_tenant_webhook.py`

**Interfaces:**
- Consumes: `TenantBillingSettings` (Task 2), `decrypt_tenant_secret` (Task 3), `process_end_customer_checkout_completed` (Task 9).
- Produces: `POST /api/v1/webhooks/stripe/tenant/{tenant_id}` — URL que cada tenant configura no próprio Dashboard Stripe.

- [ ] **Step 1: Escrever os testes**

```python
# apps/api/tests/unit/test_stripe_tenant_webhook.py
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.webhooks.stripe_tenant as webhook_module
from app.core.db import get_system_session
from app.main import app

TENANT_ID = uuid.uuid4()


def _settings_row(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        tenant_id=TENANT_ID, enabled=True, stripe_webhook_secret_encrypted="cifrado"
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


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


def test_tenant_sem_webhook_secret_configurado_retorna_400(client, session) -> None:
    session.scalar = AsyncMock(return_value=None)

    response = client.post(
        f"/api/v1/webhooks/stripe/tenant/{TENANT_ID}",
        content=b"{}",
        headers={"Stripe-Signature": "sig"},
    )

    assert response.status_code == 400


def test_assinatura_invalida_retorna_400(client, session, monkeypatch) -> None:
    session.scalar = AsyncMock(return_value=_settings_row())
    monkeypatch.setattr(webhook_module, "decrypt_tenant_secret", lambda v: "whsec_do_tenant")

    def _raise(*args, **kwargs):
        raise webhook_module.stripe.error.SignatureVerificationError("inválida", "sig")

    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", _raise)

    response = client.post(
        f"/api/v1/webhooks/stripe/tenant/{TENANT_ID}",
        content=b"{}",
        headers={"Stripe-Signature": "sig-invalida"},
    )

    assert response.status_code == 400


def test_checkout_completed_processa_evento(client, session, monkeypatch) -> None:
    session.scalar = AsyncMock(return_value=_settings_row())
    monkeypatch.setattr(webhook_module, "decrypt_tenant_secret", lambda v: "whsec_do_tenant")
    event = {"type": "checkout.session.completed", "data": {"object": {"id": "cs_1"}}}
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process = AsyncMock()
    monkeypatch.setattr(webhook_module, "process_end_customer_checkout_completed", process)

    response = client.post(
        f"/api/v1/webhooks/stripe/tenant/{TENANT_ID}",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process.assert_awaited_once()
    assert process.await_args.args[1] == TENANT_ID


def test_evento_diferente_e_ignorado(client, session, monkeypatch) -> None:
    session.scalar = AsyncMock(return_value=_settings_row())
    monkeypatch.setattr(webhook_module, "decrypt_tenant_secret", lambda v: "whsec_do_tenant")
    event = {"type": "payment_intent.succeeded", "data": {"object": {}}}
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process = AsyncMock()
    monkeypatch.setattr(webhook_module, "process_end_customer_checkout_completed", process)

    response = client.post(
        f"/api/v1/webhooks/stripe/tenant/{TENANT_ID}",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process.assert_not_awaited()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `uv run pytest tests/unit/test_stripe_tenant_webhook.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.v1.webhooks.stripe_tenant'`.

- [ ] **Step 3: Implementar**

```python
# apps/api/app/api/v1/webhooks/stripe_tenant.py
"""Webhook da Stripe de cada tenant (cobrança do cliente final).

Cada tenant configura, no próprio Dashboard Stripe, um endpoint apontando
pra /webhooks/stripe/tenant/{tenant_id} — o tenant_id na URL é só roteamento
pra achar o webhook secret certo ANTES de validar a assinatura (não é
possível "tentar" o secret de todos os tenants contra um payload).
"""

import logging
import uuid

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_tenant_secret
from app.core.db import get_system_session
from app.models import TenantBillingSettings
from app.services.end_customer_billing import process_end_customer_checkout_completed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/stripe/tenant", tags=["webhooks"])

_ASSINATURA_INVALIDA = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST, detail="Assinatura inválida"
)


@router.post("/{tenant_id}")
async def receive_tenant_webhook(
    tenant_id: uuid.UUID,
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    session: AsyncSession = Depends(get_system_session),
) -> dict:
    billing_settings = await session.scalar(
        select(TenantBillingSettings).where(TenantBillingSettings.tenant_id == tenant_id)
    )
    if billing_settings is None or billing_settings.stripe_webhook_secret_encrypted is None:
        # Mesmo erro genérico de assinatura inválida — não revela se o
        # tenant existe ou não configurou o webhook.
        raise _ASSINATURA_INVALIDA

    webhook_secret = decrypt_tenant_secret(billing_settings.stripe_webhook_secret_encrypted)
    raw_body = await request.body()
    try:
        event = stripe.Webhook.construct_event(raw_body, stripe_signature, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        logger.warning(
            "Assinatura de webhook de tenant inválida | tenant=%s erro=%s", tenant_id, exc
        )
        raise _ASSINATURA_INVALIDA

    if event["type"] == "checkout.session.completed":
        await process_end_customer_checkout_completed(session, tenant_id, event["data"]["object"])

    return {"status": "ok"}
```

- [ ] **Step 4: Registrar o router**

Em `apps/api/app/api/v1/router.py`:

```python
from app.api.v1.webhooks.stripe_tenant import router as stripe_tenant_webhook_router
```

```python
api_router.include_router(stripe_tenant_webhook_router)
```

- [ ] **Step 5: Rodar o teste de novo**

Run: `uv run pytest tests/unit/test_stripe_tenant_webhook.py -v`
Expected: PASS (todos os 4 testes).

- [ ] **Step 6: Rodar a suíte completa do `api` + lint**

Run: `uv run pytest tests/unit -q && uv run ruff check .`
Expected: todos os testes passam (incluindo os das Tasks 1-9), sem erros de lint.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/api/v1/webhooks/stripe_tenant.py apps/api/app/api/v1/router.py apps/api/tests/unit/test_stripe_tenant_webhook.py
git commit -m "feat(api): webhook por tenant da cobrança do cliente final"
```

---

## Self-Review

**Cobertura do spec** (`docs/superpowers/specs/2026-07-13-cobranca-cliente-final-design.md`): modelo de dados ✅ (Task 1-2), painel/CRUD de settings e pacotes ✅ (Task 5-6), checkout dinâmico com secret key do tenant nunca saindo do `api` ✅ (Task 7-8), webhook por tenant + confirmação ✅ (Task 9-10), segurança (Fernet própria, `INTERNAL_SERVICE_KEY`, assinatura por tenant) ✅ (Tasks 3, 4, 10).

**Fora deste plano, ficam para o Plano 2** (integração): contrato `POST /messages` do `agents` ganhando `end_customer_billing`; gate técnico em `transfer_to_specialist`; tool `gerar_link_pagamento_cliente`; débito do saldo do cliente final no `worker`; página `/configuracoes/cobranca-clientes` no `web`. Nenhuma dessas partes é executável sem este Plano 1 já mergeado (dependem das rotas/tabelas criadas aqui).

**Nota sobre a "extensibilidade"** do spec: o spec descreve `has_access`/`charge_usage` como uma interface compartilhada entre `api`/`worker`/`agents`. Como esses três são deployables separados sem código Python compartilhado (mesmo padrão já existente: `CREDIT_TOKENS_PER_CREDIT` é duplicado em `api` e `worker` hoje), a interface física só existe dentro do `api` (usada por `create_end_customer_checkout_session`, que já checa `enabled`); o `worker` (Plano 2) replica a checagem de saldo inline, do mesmo jeito que já faz hoje pro crédito do tenant. O hook de extensibilidade real é a coluna `billing_mode` — um `if` a mais no `worker` quando um segundo modo existir, não uma classe abstrata prematura.
