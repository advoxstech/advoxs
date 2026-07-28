# Assinatura mensal recorrente para o cliente final — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O cliente final de um tenant `billing_provider="connect"` pode assinar um plano mensal recorrente (acesso ilimitado, sem consumir créditos) em vez de comprar créditos avulsos — mesma lista do WhatsApp, mesmo webhook, mesmo painel de gestão de pacotes.

**Architecture:** `end_customer_credit_packages` ganha `kind` (`"one_time"`/`"subscription"`); o checkout ramifica por `kind` dentro do caminho Direct charge já existente (`mode="subscription"` + `price_data.recurring`); os 3 eventos de ciclo de vida da assinatura chegam pelo webhook único de Connect já implementado; o worker para de debitar (tenant e cliente final) enquanto o contato tiver assinatura ativa em `end_customer_subscriptions`.

**Tech Stack:** `apps/api` (FastAPI + Python 3.12, uv, Alembic — migration mais recente é `0021`), `apps/worker` (Arq, tabelas espelhadas em `apps/worker/app/tables.py`), `apps/web` (Next.js 15, Vitest).

## Global Constraints

- Spec de referência: `docs/superpowers/specs/2026-07-25-assinatura-recorrente-cliente-final-design.md`.
- Assinatura recorrente só existe pra tenants com `billing_provider="connect"` — criar um pacote `kind="subscription"` pra um tenant `standalone` é rejeitado com `409`.
- Mesmo Direct charge já implementado (`stripe_account=<acct_id>`, chave `settings.stripe_connect_secret_key`) — **sem `application_fee_amount`** em nenhum caso, igual ao pagamento avulso.
- Renovação (`invoice.payment_succeeded`) **nunca** notifica o cliente final via WhatsApp — só ativação e cancelamento notificam.
- "Ilimitado" significa nenhum débito nos dois lados (tenant e cliente final) enquanto a assinatura estiver ativa — sem teto automático nesta v1.
- `kind` é imutável após a criação do pacote — não faz parte de `EndCustomerCreditPackageUpdate`.
- 1 item desta entrega depende da forma exata de um campo da API da Stripe e tem passo de "confirmar antes de codificar" na task correspondente (Task 4 — extração do fim do período de cobrança a partir do `Invoice`).

---

## Task 1: Modelo de dados — `kind`, `end_customer_subscriptions`

**Files:**
- Create: `apps/api/alembic/versions/0022_assinatura_recorrente_cliente_final.py`
- Modify: `apps/api/app/models/end_customer_billing.py` (`EndCustomerCreditPackage`, nova classe `EndCustomerSubscription`)
- Modify: `apps/api/app/models/__init__.py`
- Modify: `apps/api/app/schemas/end_customer_billing.py` (`EndCustomerCreditPackageOut`/`In`)
- Modify: `apps/worker/app/tables.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_packages_routes.py`

**Interfaces:**
- Produces: `EndCustomerCreditPackage.kind: str` (default `"one_time"`), `EndCustomerCreditPackage.credits_granted: int | None` (nullable agora); classe `EndCustomerSubscription` (`id`, `tenant_id`, `contact_phone_number`, `end_customer_credit_package_id`, `stripe_subscription_id`, `status`, `current_period_end`, `created_at`, `updated_at`). `EndCustomerCreditPackageOut.kind: str`, `.credits_granted: int | None`. `EndCustomerCreditPackageIn.kind: str = "one_time"`, `.credits_granted: int | None`, com validação: `credits_granted` obrigatório quando `kind="one_time"`.

- [ ] **Step 1: Escrever a migration**

```python
"""Assinatura mensal recorrente pro cliente final — end_customer_credit_packages
ganha `kind` ("one_time"|"subscription"); credits_granted deixa de ser
obrigatório (só faz sentido pra kind="one_time"); tabela nova
end_customer_subscriptions guarda o ciclo de vida da assinatura ativa por
contato — ver docs/superpowers/specs/2026-07-25-assinatura-recorrente-cliente-final-design.md.

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-25
"""

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "end_customer_credit_packages",
        sa.Column("kind", sa.String(), server_default=sa.text("'one_time'"), nullable=False),
    )
    op.alter_column("end_customer_credit_packages", "credits_granted", nullable=True)

    op.create_table(
        "end_customer_subscriptions",
        sa.Column(
            "id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, index=True
        ),
        sa.Column("contact_phone_number", sa.String(), nullable=False),
        sa.Column(
            "end_customer_credit_package_id",
            sa.Uuid(),
            sa.ForeignKey("end_customer_credit_packages.id"),
            nullable=True,
        ),
        sa.Column("stripe_subscription_id", sa.String(), nullable=False, unique=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_unique_constraint(
        "uq_end_customer_subscriptions_tenant_contact",
        "end_customer_subscriptions",
        ["tenant_id", "contact_phone_number"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_end_customer_subscriptions_tenant_contact",
        "end_customer_subscriptions",
        type_="unique",
    )
    op.drop_table("end_customer_subscriptions")
    op.alter_column("end_customer_credit_packages", "credits_granted", nullable=False)
    op.drop_column("end_customer_credit_packages", "kind")
```

- [ ] **Step 2: Rodar a migration**

Run: `cd apps/api && uv run alembic upgrade head`
Expected: sem erro; `uv run alembic current` mostra `0022`.

- [ ] **Step 3: Escrever o teste que falha (schema — `kind`/`credits_granted` condicional)**

Adicionar a `apps/api/tests/unit/test_end_customer_billing_packages_routes.py`:

```python
def test_create_pacote_avulso_sem_credits_granted_retorna_422(client, session) -> None:
    response = client.post(
        "/api/v1/end-customer-billing/packages",
        json={"name": "Básico", "price_brl": "49.90", "kind": "one_time"},
    )

    assert response.status_code == 422


def test_create_pacote_assinatura_sem_credits_granted_funciona(client, session) -> None:
    session.scalar.return_value = SimpleNamespace(billing_provider="connect")
    added = []
    session.add = MagicMock(side_effect=lambda obj: added.append(obj))

    async def fake_refresh(obj):
        obj.id = PACKAGE_ID

    session.refresh.side_effect = fake_refresh

    response = client.post(
        "/api/v1/end-customer-billing/packages",
        json={"name": "Ilimitado", "price_brl": "49.90", "kind": "subscription"},
    )

    assert response.status_code == 201
    assert added[0].kind == "subscription"
    assert added[0].credits_granted is None


def test_create_pacote_kind_invalido_retorna_422(client, session) -> None:
    response = client.post(
        "/api/v1/end-customer-billing/packages",
        json={"name": "X", "price_brl": "49.90", "kind": "vitalicio", "credits_granted": 10},
    )

    assert response.status_code == 422
```

- [ ] **Step 4: Rodar os testes e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_packages_routes.py -v -k kind`
Expected: falha — `kind`/validação condicional não existem ainda no schema; `test_create_pacote_avulso_sem_credits_granted_retorna_422` falha porque hoje `credits_granted` é sempre obrigatório mas sem o campo `kind` retornaria 422 por outro motivo (campo desconhecido é ignorado pelo Pydantic sem `extra="forbid"` — na prática falharia por `credits_granted` ausente de qualquer forma; confirmar a mensagem real ao rodar). `test_create_pacote_assinatura_sem_credits_granted_funciona` falha porque `kind` não é persistido em lugar nenhum (o model não tem a coluna ainda antes do Step 5).

- [ ] **Step 5: Atualizar o model `EndCustomerCreditPackage` e criar `EndCustomerSubscription`**

Em `apps/api/app/models/end_customer_billing.py`, alterar a classe `EndCustomerCreditPackage` (linhas 58-72 do arquivo atual):

```python
class EndCustomerCreditPackage(Base):
    """Pacote de créditos (kind="one_time") ou assinatura mensal recorrente
    (kind="subscription", só disponível pra tenants billing_provider="connect"
    — ver app/api/v1/end_customer_billing.py) que o tenant vende aos próprios
    clientes finais."""

    __tablename__ = "end_customer_credit_packages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    price_brl: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'one_time'"))
    credits_granted: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
```

E adicionar, depois de `EndCustomerCreditTransaction` (final do arquivo):

```python
class EndCustomerSubscription(Base):
    """Assinatura mensal recorrente ativa (ou já cancelada) de um cliente
    final com um tenant — equivalente, pra assinatura, do que
    `EndCustomerBalance` é pro saldo de créditos avulsos."""

    __tablename__ = "end_customer_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "contact_phone_number", name="uq_end_customer_subscriptions_tenant_contact"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    contact_phone_number: Mapped[str] = mapped_column(String, nullable=False)
    end_customer_credit_package_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("end_customer_credit_packages.id")
    )
    stripe_subscription_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
```

Adicionar `UniqueConstraint` ao bloco de imports do topo do arquivo (linha 5-16 atual):

```python
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
```

- [ ] **Step 6: Registrar `EndCustomerSubscription` em `app/models/__init__.py`**

Adicionar `EndCustomerSubscription` ao import de `app.models.end_customer_billing` e ao `__all__`, mesmo padrão das outras 3 classes já importadas de lá.

- [ ] **Step 7: Atualizar os schemas**

Em `apps/api/app/schemas/end_customer_billing.py`, adicionar `model_validator` ao import do topo:

```python
from pydantic import BaseModel, ConfigDict, Field, model_validator
```

Substituir `EndCustomerCreditPackageOut`/`EndCustomerCreditPackageIn` (linhas 37-51 do arquivo atual):

```python
class EndCustomerCreditPackageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    price_brl: Decimal
    kind: str
    credits_granted: int | None
    active: bool


class EndCustomerCreditPackageIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    price_brl: Decimal = Field(gt=0)
    kind: str = Field(default="one_time")
    credits_granted: int | None = Field(default=None, gt=0)
    active: bool = True

    @model_validator(mode="after")
    def _valida_kind_e_credits_granted(self) -> "EndCustomerCreditPackageIn":
        if self.kind not in ("one_time", "subscription"):
            raise ValueError("kind deve ser 'one_time' ou 'subscription'")
        if self.kind == "one_time" and self.credits_granted is None:
            raise ValueError("credits_granted é obrigatório para pacotes avulsos (kind=one_time)")
        return self
```

(`EndCustomerCreditPackageUpdate`, linhas 54-58, não muda — `kind` é imutável após criação, não faz parte deste schema.)

- [ ] **Step 8: Espelhar em `apps/worker/app/tables.py`**

Modificar a tabela `end_customer_credit_packages` (linhas 132-141 atuais) — adicionar a coluna `kind`:

```python
end_customer_credit_packages = Table(
    "end_customer_credit_packages",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("name", String),
    Column("price_brl", Numeric(10, 2)),
    Column("kind", String),
    Column("credits_granted", Integer),
    Column("active", Boolean),
)
```

E adicionar a tabela nova, depois de `end_customer_credit_transactions` (final do arquivo):

```python
end_customer_subscriptions = Table(
    "end_customer_subscriptions",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("contact_phone_number", String),
    Column("status", String),
    Column("current_period_end", DateTime(timezone=True)),
)
```

(só as colunas que o worker de fato lê — `end_customer_credit_package_id`/`stripe_subscription_id`/`created_at`/`updated_at` não são usadas por nenhum job do worker nesta feature, mesmo princípio já documentado no topo do arquivo: "aqui só as colunas usadas pelos jobs".)

- [ ] **Step 9: Rodar os testes e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_packages_routes.py -v`
Expected: todos passam, incluindo os 3 novos.

- [ ] **Step 10: Rodar a suíte completa do `apps/api` e `apps/worker` pra checar regressão**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Run: `cd apps/worker && python3 -m pytest tests/unit -q` (⚠️ venv do `apps/worker` é quebrado — nunca usar `uv run` nesse app, sempre `python3 -m pytest`/`python3 -m ruff` direto)
Expected: sem falhas novas em nenhum dos dois (o worker ainda não lê `kind`, só precisa continuar importando `tables.py` sem erro de sintaxe).

- [ ] **Step 11: Commit**

```bash
cd apps/api
git add alembic/versions/0022_assinatura_recorrente_cliente_final.py app/models/end_customer_billing.py app/models/__init__.py app/schemas/end_customer_billing.py tests/unit/test_end_customer_billing_packages_routes.py
cd ../worker
git add app/tables.py
cd ..
git commit -m "feat(api): kind em end_customer_credit_packages + tabela end_customer_subscriptions"
```

---

## Task 2: Restrição de escopo — `kind="subscription"` só pra tenants `connect`

**Files:**
- Modify: `apps/api/app/api/v1/end_customer_billing.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_packages_routes.py`

**Interfaces:**
- Consumes: `TenantBillingSettings.billing_provider` (Task 1 da migração Connect, já mergeado).
- Produces: `POST /end-customer-billing/packages` rejeita (`409`) `kind="subscription"` quando o tenant não é `billing_provider="connect"`.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar a `apps/api/tests/unit/test_end_customer_billing_packages_routes.py`:

```python
def test_create_pacote_assinatura_em_tenant_standalone_retorna_409(client, session) -> None:
    session.scalar.return_value = SimpleNamespace(billing_provider="standalone")

    response = client.post(
        "/api/v1/end-customer-billing/packages",
        json={"name": "Ilimitado", "price_brl": "49.90", "kind": "subscription"},
    )

    assert response.status_code == 409


def test_create_pacote_assinatura_sem_configuracao_alguma_retorna_409(client, session) -> None:
    """Tenant que nunca configurou billing (sem linha em tenant_billing_settings)
    tem billing_provider default "standalone" — mesma rejeição."""
    session.scalar.return_value = None

    response = client.post(
        "/api/v1/end-customer-billing/packages",
        json={"name": "Ilimitado", "price_brl": "49.90", "kind": "subscription"},
    )

    assert response.status_code == 409


def test_create_pacote_avulso_em_tenant_standalone_funciona(client, session) -> None:
    """kind="one_time" (o default) nunca é restrito por billing_provider —
    só verifica billing_provider quando kind="subscription"."""
    added = []
    session.add = MagicMock(side_effect=lambda obj: added.append(obj))

    async def fake_refresh(obj):
        obj.id = PACKAGE_ID

    session.refresh.side_effect = fake_refresh

    response = client.post(
        "/api/v1/end-customer-billing/packages",
        json={"name": "Básico", "price_brl": "49.90", "credits_granted": 500},
    )

    assert response.status_code == 201
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_packages_routes.py -v -k tenant`
Expected: os 2 primeiros falham (retornam `201` hoje, sem a checagem); o terceiro já passa (nenhuma mudança de comportamento pra `kind="one_time"`) — confirmar que passa antes de seguir, pra não confundir com uma falha real depois do Step 3.

- [ ] **Step 3: Implementar a checagem em `create_package`**

Em `apps/api/app/api/v1/end_customer_billing.py`, adicionar um helper antes de `create_package` (depois de `_get_settings_row`):

```python
async def _get_billing_provider(ctx: TenantContext, session: AsyncSession) -> str:
    row = await _get_settings_row(ctx, session)
    return row.billing_provider if row is not None else "standalone"
```

E modificar `create_package` (linhas 166-176 atuais):

```python
@router.post("/packages", status_code=status.HTTP_201_CREATED)
async def create_package(
    body: EndCustomerCreditPackageIn,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> EndCustomerCreditPackageOut:
    if body.kind == "subscription" and await _get_billing_provider(ctx, session) != "connect":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assinatura recorrente só está disponível pra tenants configurados via Stripe Connect",
        )
    package = EndCustomerCreditPackage(tenant_id=ctx.tenant_id, **body.model_dump())
    session.add(package)
    await session.commit()
    await session.refresh(package)
    return EndCustomerCreditPackageOut.model_validate(package)
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_packages_routes.py -v`
Expected: todos passam.

- [ ] **Step 5: Rodar a suíte completa e ruff**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Expected: sem falhas novas.

- [ ] **Step 6: Commit**

```bash
cd apps/api
git add app/api/v1/end_customer_billing.py tests/unit/test_end_customer_billing_packages_routes.py
git commit -m "feat(api): restringe kind=subscription a tenants billing_provider=connect"
```

---

## Task 3: Checkout — `mode="subscription"` pra pacotes recorrentes

**Files:**
- Modify: `apps/api/app/services/end_customer_billing.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_service.py`

**Interfaces:**
- Consumes: `EndCustomerCreditPackage.kind` (Task 1).
- Produces: `create_end_customer_checkout_session` mantém a mesma assinatura; ramifica internamente por `package.kind` — `mode="subscription"` + `price_data.recurring={"interval": "month"}` quando `kind="subscription"`, metadata `kind="end_customer_subscription"` em vez de `"end_customer_purchase"`.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar a `apps/api/tests/unit/test_end_customer_billing_service.py`, dentro de `class TestCreateEndCustomerCheckoutSession` (seguir o padrão dos testes já existentes na classe — `session.scalar = AsyncMock(side_effect=[...])`, mock de `service.stripe.checkout.Session.create`):

```python
    async def test_checkout_de_assinatura_usa_mode_subscription_e_recurring(
        self, session, monkeypatch
    ) -> None:
        session.scalar = AsyncMock(
            side_effect=[
                _settings_row(
                    billing_provider="connect",
                    stripe_account_id="acct_123",
                    stripe_account_status="active",
                ),
                _package(kind="subscription", credits_granted=None),
            ]
        )
        created = MagicMock(
            return_value=SimpleNamespace(url="https://checkout.stripe.com/pay/cs_sub_1")
        )
        monkeypatch.setattr(service.stripe.checkout.Session, "create", created)

        url = await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

        assert url == "https://checkout.stripe.com/pay/cs_sub_1"
        kwargs = created.call_args.kwargs
        assert kwargs["mode"] == "subscription"
        assert kwargs["line_items"][0]["price_data"]["recurring"] == {"interval": "month"}
        assert kwargs["metadata"]["kind"] == "end_customer_subscription"
        assert "application_fee_amount" not in kwargs

    async def test_checkout_de_pacote_avulso_continua_mode_payment(
        self, session, monkeypatch
    ) -> None:
        session.scalar = AsyncMock(
            side_effect=[
                _settings_row(
                    billing_provider="connect",
                    stripe_account_id="acct_123",
                    stripe_account_status="active",
                ),
                _package(kind="one_time"),
            ]
        )
        created = MagicMock(
            return_value=SimpleNamespace(url="https://checkout.stripe.com/pay/cs_one_1")
        )
        monkeypatch.setattr(service.stripe.checkout.Session, "create", created)

        await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

        kwargs = created.call_args.kwargs
        assert kwargs["mode"] == "payment"
        assert "recurring" not in kwargs["line_items"][0]["price_data"]
        assert kwargs["metadata"]["kind"] == "end_customer_purchase"
```

(`_package(**overrides)` já existe no arquivo, com default `active=True` — os overrides `kind`/`credits_granted` passam a existir como atributos do `SimpleNamespace` normalmente, sem precisar tocar no helper.)

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py -v -k assinatura`
Expected: falha — hoje `mode` é sempre `"payment"`, sem `recurring`, e `metadata["kind"]` é sempre `"end_customer_purchase"`.

- [ ] **Step 3: Implementar a ramificação por `kind`**

Em `apps/api/app/services/end_customer_billing.py`, modificar o bloco de construção de `line_items`/`metadata` em `create_end_customer_checkout_session` (as linhas entre a checagem de pacote e o `try`):

```python
    if package.kind == "subscription":
        price_data = {
            "currency": "brl",
            "unit_amount": int(package.price_brl * 100),
            "product_data": {"name": package.name},
            "recurring": {"interval": "month"},
        }
        mode = "subscription"
        checkout_kind = "end_customer_subscription"
    else:
        price_data = {
            "currency": "brl",
            "unit_amount": int(package.price_brl * 100),
            "product_data": {"name": package.name},
        }
        mode = "payment"
        checkout_kind = "end_customer_purchase"

    line_items = [{"price_data": price_data, "quantity": 1}]
    metadata = {
        "tenant_id": str(tenant_id),
        "contact_phone_number": contact_phone_number,
        "package_id": str(package_id),
        "kind": checkout_kind,
    }
```

E trocar `mode="payment"` (literal, hardcoded 2x — uma no branch `connect`, outra no branch `else`/standalone) por `mode=mode` nas duas chamadas de `stripe.checkout.Session.create` já existentes.

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py -v`
Expected: todos passam, incluindo os 2 novos e todos os pré-existentes (que exercitam `kind="one_time"` implicitamente via `_package()` sem override — confirmar que o default de `_package()` já é `kind="one_time"`, herdado do `server_default` da coluna refletido no model; se o `SimpleNamespace` do helper `_package` não tiver `kind` no dict base, adicionar `kind="one_time"` ao dict base do helper, mesmo padrão de default explícito já usado nos outros helpers deste arquivo — ver `_settings_row`).

- [ ] **Step 5: Rodar a suíte completa e ruff**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Expected: sem falhas novas.

- [ ] **Step 6: Commit**

```bash
cd apps/api
git add app/services/end_customer_billing.py tests/unit/test_end_customer_billing_service.py
git commit -m "feat(api): checkout do cliente final ramifica por kind (mode=subscription pra assinatura)"
```

---

## Task 4: Webhook — ativação, renovação e cancelamento de assinatura

**Files:**
- Modify: `apps/api/app/services/end_customer_billing.py`
- Modify: `apps/api/app/api/v1/webhooks/stripe_connect.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_service.py`
- Test: `apps/api/tests/unit/test_stripe_connect_webhook.py`

**Interfaces:**
- Consumes: `EndCustomerSubscription` (Task 1).
- Produces: `process_end_customer_subscription_created(session, tenant_id, stripe_session: dict) -> None`, `process_end_customer_subscription_renewed(session, tenant_id, invoice: dict) -> None`, `process_end_customer_subscription_status_changed(session, tenant_id, subscription_payload: dict, *, notify_cancel: bool) -> None` — todas em `apps/api/app/services/end_customer_billing.py`. Webhook chama as 3 a partir dos eventos `checkout.session.completed` (metadata `kind="end_customer_subscription"`), `invoice.payment_succeeded` e `customer.subscription.deleted`/`.updated`.

- [ ] **Step 1 — CONFIRMAR ANTES DE CODIFICAR: onde está o fim do período de cobrança no payload de `invoice.payment_succeeded`**

O `Invoice` da Stripe não tem um campo `period_end` no nível raiz do objeto — o fim do período de cobrança fica em `invoice["lines"]["data"][0]["period"]["end"]` (unix timestamp), dentro da primeira linha de fatura. Confirmar isso contra a doc atual da Stripe (referência de partida: `docs.stripe.com/api/invoices/object`, seção `lines`) ou introspecção de um `Invoice` real construído via `stripe.Invoice.construct_from` no SDK instalado, antes de escrever o parsing no Step 5. Se a estrutura real divergir (ex: campo renomeado, formato de fatura com múltiplas linhas de period diferentes), ajustar `_extract_period_end` (Step 5) de acordo — documentar a fonte consultada no relatório da task.

- [ ] **Step 2: Refatorar `_send_purchase_confirmation` num helper reaproveitável**

Antes de adicionar as notificações novas (ativação/cancelamento de assinatura), extrair a lógica comum de "enviar texto fixo via WhatsApp + registrar `Message` + opcionalmente sair do billing gate" — hoje só usada por `_send_purchase_confirmation`, mas as 2 notificações novas desta task repetiriam o mesmo bloco inteiro se não for extraído primeiro.

Em `apps/api/app/services/end_customer_billing.py`, substituir `_send_purchase_confirmation` (função completa, linhas 217-283 do arquivo atual) por:

```python
async def _notify_end_customer(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    contact_phone_number: str,
    text: str,
    *,
    exit_billing_gate: bool,
) -> None:
    """Notificação fixa via WhatsApp, best-effort — uma falha no envio nunca
    desfaz o efeito que já foi commitado antes desta chamada (crédito
    concedido, assinatura ativada/cancelada). Reaproveitada pela confirmação
    de compra avulsa, ativação e cancelamento de assinatura — só o texto e a
    decisão de sair do billing gate mudam entre os 3 casos."""
    try:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.contact_phone_number == contact_phone_number,
            )
        )
        number = await session.scalar(
            select(WhatsAppNumber).where(
                WhatsAppNumber.tenant_id == tenant_id, WhatsAppNumber.status == "connected"
            )
        )
        if number is None or conversation is None:
            logger.warning(
                "Sem número/conversa pra notificar o cliente final | tenant=%s contato=%s",
                tenant_id,
                contact_phone_number,
            )
            return

        await send_text_message(
            phone_number_id=number.phone_number_id,
            access_token=decrypt_access_token(number.access_token_encrypted),
            to=contact_phone_number,
            text=text,
        )

        session.add(
            Message(
                conversation_id=conversation.id,
                tenant_id=tenant_id,
                sender_type="system",
                content=text,
                delivery_status="sent",
            )
        )
        conversation.last_message_at = datetime.now(UTC)

        if exit_billing_gate and conversation.state == "billing_gate":
            conversation.state = "agent"
            conversation.billing_gate_step = None
            conversation.billing_gate_retries = 0
        await session.commit()
    except WhatsAppSendError:
        logger.exception(
            "Falha ao notificar o cliente final via WhatsApp | tenant=%s contato=%s",
            tenant_id,
            contact_phone_number,
        )
    except Exception:
        logger.exception(
            "Erro inesperado ao notificar o cliente final | tenant=%s contato=%s",
            tenant_id,
            contact_phone_number,
        )
```

E, no corpo de `process_end_customer_checkout_completed` (linha 214 do arquivo atual), trocar a chamada:

```python
    await _send_purchase_confirmation(session, tenant_id, contact_phone_number)
```

por:

```python
    await _notify_end_customer(
        session,
        tenant_id,
        contact_phone_number,
        "Pagamento confirmado! Você já pode continuar a conversa.",
        exit_billing_gate=True,
    )
```

- [ ] **Step 3: Rodar os testes existentes e confirmar que passam sem alteração**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py -v -k confirmation`
Expected: os testes que já cobrem `_send_purchase_confirmation` continuam passando idênticos — se algum monkeypatch referenciar `service._send_purchase_confirmation` pelo nome antigo, atualizar pra `service._notify_end_customer` (ler o arquivo de teste antes de assumir; se nenhum teste faz monkeypatch direto dessa função interna, nada mais precisa mudar aqui).

- [ ] **Step 4: Escrever os testes que falham (as 3 funções novas)**

Adicionar a `apps/api/tests/unit/test_end_customer_billing_service.py`:

```python
class TestProcessEndCustomerSubscriptionCreated:
    async def test_cria_assinatura_e_notifica(self, session, monkeypatch) -> None:
        session.scalar = AsyncMock(return_value=None)
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        notify = AsyncMock()
        monkeypatch.setattr(service, "_notify_end_customer", notify)
        stripe_session = {
            "id": "cs_sub_1",
            "subscription": "sub_123",
            "metadata": {
                "kind": "end_customer_subscription",
                "contact_phone_number": CONTACT,
                "package_id": str(PACKAGE_ID),
            },
        }

        await process_end_customer_subscription_created(session, TENANT_ID, stripe_session)

        assert len(added) == 1
        created = added[0]
        assert created.tenant_id == TENANT_ID
        assert created.contact_phone_number == CONTACT
        assert created.stripe_subscription_id == "sub_123"
        assert created.status == "active"
        assert created.end_customer_credit_package_id == PACKAGE_ID
        notify.assert_awaited_once()
        assert notify.await_args.args[3] == "Assinatura ativada! Você já tem acesso ilimitado."
        assert notify.await_args.kwargs["exit_billing_gate"] is True

    async def test_duplicado_por_stripe_subscription_id_e_ignorado(self, session, monkeypatch) -> None:
        session.scalar = AsyncMock(return_value=uuid.uuid4())
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        notify = AsyncMock()
        monkeypatch.setattr(service, "_notify_end_customer", notify)

        await process_end_customer_subscription_created(
            session, TENANT_ID, {"id": "cs_sub_1", "subscription": "sub_123", "metadata": {}}
        )

        assert added == []
        notify.assert_not_awaited()

    async def test_metadata_de_compra_avulsa_e_ignorada(self, session, monkeypatch) -> None:
        session.scalar = AsyncMock(return_value=None)
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        await process_end_customer_subscription_created(
            session,
            TENANT_ID,
            {
                "id": "cs_1",
                "subscription": None,
                "metadata": {"kind": "end_customer_purchase"},
            },
        )

        assert added == []


class TestProcessEndCustomerSubscriptionRenewed:
    async def test_atualiza_current_period_end_sem_notificar(self, session, monkeypatch) -> None:
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            stripe_subscription_id="sub_123",
            status="past_due",
            current_period_end=None,
        )
        session.scalar = AsyncMock(return_value=subscription)
        notify = AsyncMock()
        monkeypatch.setattr(service, "_notify_end_customer", notify)
        invoice = {
            "subscription": "sub_123",
            "lines": {"data": [{"period": {"end": 1735689600}}]},
        }

        await process_end_customer_subscription_renewed(session, TENANT_ID, invoice)

        assert subscription.status == "active"
        assert subscription.current_period_end == datetime.fromtimestamp(1735689600, UTC)
        session.commit.assert_awaited_once()
        notify.assert_not_awaited()

    async def test_assinatura_nao_encontrada_e_ignorado(self, session) -> None:
        session.scalar = AsyncMock(return_value=None)

        await process_end_customer_subscription_renewed(
            session, TENANT_ID, {"subscription": "sub_desconhecida", "lines": {"data": []}}
        )

        session.commit.assert_not_awaited()


class TestProcessEndCustomerSubscriptionStatusChanged:
    async def test_cancelamento_notifica(self, session, monkeypatch) -> None:
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            stripe_subscription_id="sub_123",
            status="active",
        )
        session.scalar = AsyncMock(return_value=subscription)
        notify = AsyncMock()
        monkeypatch.setattr(service, "_notify_end_customer", notify)

        await process_end_customer_subscription_status_changed(
            session, TENANT_ID, {"id": "sub_123", "status": "canceled"}, notify_cancel=True
        )

        assert subscription.status == "canceled"
        session.commit.assert_awaited_once()
        notify.assert_awaited_once()
        assert notify.await_args.args[3] == (
            "Sua assinatura mensal foi cancelada — o atendimento volta a consumir "
            "créditos normalmente."
        )

    async def test_atualizacao_sem_cancelamento_nao_notifica(self, session, monkeypatch) -> None:
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            stripe_subscription_id="sub_123",
            status="active",
        )
        session.scalar = AsyncMock(return_value=subscription)
        notify = AsyncMock()
        monkeypatch.setattr(service, "_notify_end_customer", notify)

        await process_end_customer_subscription_status_changed(
            session, TENANT_ID, {"id": "sub_123", "status": "past_due"}, notify_cancel=False
        )

        assert subscription.status == "past_due"
        notify.assert_not_awaited()
```

(Adicionar `from datetime import UTC, datetime` e os imports de `process_end_customer_subscription_created`/`_renewed`/`_status_changed` ao topo do arquivo de teste, junto dos já existentes — conferir o que já está importado antes de duplicar.)

- [ ] **Step 5: Rodar os testes e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py -v -k Subscription`
Expected: `ImportError`/`AttributeError` — as 3 funções não existem ainda.

- [ ] **Step 6: Implementar as 3 funções**

Em `apps/api/app/services/end_customer_billing.py`, adicionar `EndCustomerSubscription` ao import de `app.models` (linha 20-28 atual) e adicionar, depois de `process_end_customer_checkout_completed`:

```python
async def process_end_customer_subscription_created(
    session: AsyncSession, tenant_id: uuid.UUID, stripe_session: dict
) -> None:
    """Ativa a assinatura recorrente do cliente final e confirma via WhatsApp.

    Idempotente por stripe_subscription_id — mesmo padrão de idempotência já
    usado pra compra avulsa (lá por stripe_payment_id)."""
    subscription_id = stripe_session.get("subscription")
    if not subscription_id:
        return
    already_processed = await session.scalar(
        select(EndCustomerSubscription.id).where(
            EndCustomerSubscription.stripe_subscription_id == subscription_id
        )
    )
    if already_processed is not None:
        logger.info(
            "Webhook de assinatura duplicado, ignorando | subscription=%s", subscription_id
        )
        return

    raw_metadata = stripe_session["metadata"] if "metadata" in stripe_session else {}
    metadata = raw_metadata.to_dict() if hasattr(raw_metadata, "to_dict") else dict(raw_metadata)

    if metadata.get("kind") != "end_customer_subscription":
        return

    contact_phone_number = metadata.get("contact_phone_number")
    package_id_raw = metadata.get("package_id")
    if not contact_phone_number or not package_id_raw:
        logger.error("Metadata incompleta no webhook de assinatura | subscription=%s", subscription_id)
        return

    session.add(
        EndCustomerSubscription(
            tenant_id=tenant_id,
            contact_phone_number=contact_phone_number,
            end_customer_credit_package_id=uuid.UUID(package_id_raw),
            stripe_subscription_id=subscription_id,
            status="active",
        )
    )
    await session.commit()

    await _notify_end_customer(
        session,
        tenant_id,
        contact_phone_number,
        "Assinatura ativada! Você já tem acesso ilimitado.",
        exit_billing_gate=True,
    )


def _extract_period_end(invoice: dict) -> datetime | None:
    """Fim do ciclo de cobrança pago — fica em lines.data[0].period.end
    (unix timestamp), não num campo period_end de nível raiz do Invoice (ver
    Step 1 desta task no plano de implementação pra confirmação da fonte)."""
    lines = invoice.get("lines") or {}
    lines_data = lines.get("data") if hasattr(lines, "get") else None
    if not lines_data:
        return None
    period = lines_data[0].get("period") or {}
    end_timestamp = period.get("end")
    if end_timestamp is None:
        return None
    return datetime.fromtimestamp(end_timestamp, UTC)


async def process_end_customer_subscription_renewed(
    session: AsyncSession, tenant_id: uuid.UUID, invoice: dict
) -> None:
    """Renovação mensal — atualiza status/current_period_end, sem notificar
    o cliente (decisão deliberada: renovação silenciosa evita spam mensal)."""
    subscription_id = invoice.get("subscription")
    if not subscription_id:
        return
    subscription = await session.scalar(
        select(EndCustomerSubscription).where(
            EndCustomerSubscription.tenant_id == tenant_id,
            EndCustomerSubscription.stripe_subscription_id == subscription_id,
        )
    )
    if subscription is None:
        logger.warning(
            "Renovação de assinatura desconhecida | tenant=%s subscription=%s",
            tenant_id,
            subscription_id,
        )
        return

    subscription.status = "active"
    period_end = _extract_period_end(invoice)
    if period_end is not None:
        subscription.current_period_end = period_end
    subscription.updated_at = datetime.now(UTC)
    await session.commit()


async def process_end_customer_subscription_status_changed(
    session: AsyncSession, tenant_id: uuid.UUID, subscription_payload: dict, *, notify_cancel: bool
) -> None:
    """`customer.subscription.deleted` (cancelamento, notifica) ou
    `customer.subscription.updated` (ex: past_due, não notifica)."""
    subscription_id = subscription_payload.get("id")
    if not subscription_id:
        return
    subscription = await session.scalar(
        select(EndCustomerSubscription).where(
            EndCustomerSubscription.tenant_id == tenant_id,
            EndCustomerSubscription.stripe_subscription_id == subscription_id,
        )
    )
    if subscription is None:
        logger.warning(
            "Mudança de status de assinatura desconhecida | tenant=%s subscription=%s",
            tenant_id,
            subscription_id,
        )
        return

    subscription.status = subscription_payload.get("status", subscription.status)
    subscription.updated_at = datetime.now(UTC)
    contact_phone_number = subscription.contact_phone_number
    await session.commit()

    if notify_cancel:
        await _notify_end_customer(
            session,
            tenant_id,
            contact_phone_number,
            "Sua assinatura mensal foi cancelada — o atendimento volta a consumir créditos normalmente.",
            exit_billing_gate=False,
        )
```

- [ ] **Step 7: Rodar os testes e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py -v`
Expected: todos passam.

- [ ] **Step 8: Escrever o teste que falha (roteamento no webhook)**

Adicionar a `apps/api/tests/unit/test_stripe_connect_webhook.py`:

```python
def test_checkout_completed_de_assinatura_chama_process_subscription_created(
    client, session, monkeypatch
):
    session.scalar = AsyncMock(
        return_value=SimpleNamespace(tenant_id=TENANT_ID, stripe_account_id=ACCOUNT_ID)
    )
    event = {
        "type": "checkout.session.completed",
        "account": ACCOUNT_ID,
        "data": {"object": {"id": "cs_1", "subscription": "sub_1"}},
    }
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process_purchase = AsyncMock()
    process_subscription = AsyncMock()
    monkeypatch.setattr(webhook_module, "process_end_customer_checkout_completed", process_purchase)
    monkeypatch.setattr(
        webhook_module, "process_end_customer_subscription_created", process_subscription
    )

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process_purchase.assert_awaited_once()
    process_subscription.assert_awaited_once()


def test_invoice_payment_succeeded_chama_process_renewed(client, session, monkeypatch):
    session.scalar = AsyncMock(
        return_value=SimpleNamespace(tenant_id=TENANT_ID, stripe_account_id=ACCOUNT_ID)
    )
    event = {
        "type": "invoice.payment_succeeded",
        "account": ACCOUNT_ID,
        "data": {"object": {"subscription": "sub_1"}},
    }
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process_renewed = AsyncMock()
    monkeypatch.setattr(webhook_module, "process_end_customer_subscription_renewed", process_renewed)

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process_renewed.assert_awaited_once()
    assert process_renewed.await_args.args[1] == TENANT_ID


def test_customer_subscription_deleted_notifica_cancelamento(client, session, monkeypatch):
    session.scalar = AsyncMock(
        return_value=SimpleNamespace(tenant_id=TENANT_ID, stripe_account_id=ACCOUNT_ID)
    )
    event = {
        "type": "customer.subscription.deleted",
        "account": ACCOUNT_ID,
        "data": {"object": {"id": "sub_1", "status": "canceled"}},
    }
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process_status = AsyncMock()
    monkeypatch.setattr(
        webhook_module, "process_end_customer_subscription_status_changed", process_status
    )

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process_status.assert_awaited_once()
    assert process_status.await_args.kwargs["notify_cancel"] is True


def test_customer_subscription_updated_nao_notifica_cancelamento(client, session, monkeypatch):
    session.scalar = AsyncMock(
        return_value=SimpleNamespace(tenant_id=TENANT_ID, stripe_account_id=ACCOUNT_ID)
    )
    event = {
        "type": "customer.subscription.updated",
        "account": ACCOUNT_ID,
        "data": {"object": {"id": "sub_1", "status": "past_due"}},
    }
    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", lambda *a, **k: event)
    process_status = AsyncMock()
    monkeypatch.setattr(
        webhook_module, "process_end_customer_subscription_status_changed", process_status
    )

    response = client.post(
        "/api/v1/webhooks/stripe/connect",
        content=b"{}",
        headers={"Stripe-Signature": "sig-valida"},
    )

    assert response.status_code == 200
    process_status.assert_awaited_once()
    assert process_status.await_args.kwargs["notify_cancel"] is False
```

- [ ] **Step 9: Rodar os testes e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_stripe_connect_webhook.py -v -k "assinatura or renewed or subscription"`
Expected: falha — o roteamento novo não existe ainda; `test_checkout_completed_de_assinatura_chama_process_subscription_created` também falha porque `process_end_customer_subscription_created` não está importado no módulo do webhook.

- [ ] **Step 10: Implementar o roteamento no webhook**

Em `apps/api/app/api/v1/webhooks/stripe_connect.py`, ampliar o import de `app.services.end_customer_billing`:

```python
from app.services.end_customer_billing import (
    process_end_customer_checkout_completed,
    process_end_customer_subscription_created,
    process_end_customer_subscription_renewed,
    process_end_customer_subscription_status_changed,
)
```

E o corpo de `receive_connect_webhook` (bloco `if event["type"] == "checkout.session.completed": ...`, linhas 102-109 atuais):

```python
    if event["type"] == "checkout.session.completed":
        # Cada função checa a própria metadata.kind e não faz nada se não
        # bater — compra avulsa e assinatura são o mesmo evento Stripe,
        # diferenciados só pela metadata, nunca pelo type do evento.
        await process_end_customer_checkout_completed(
            session, billing_settings.tenant_id, event["data"]["object"]
        )
        await process_end_customer_subscription_created(
            session, billing_settings.tenant_id, event["data"]["object"]
        )
    elif event["type"] == "invoice.payment_succeeded":
        await process_end_customer_subscription_renewed(
            session, billing_settings.tenant_id, event["data"]["object"]
        )
    elif event["type"] in ("customer.subscription.deleted", "customer.subscription.updated"):
        await process_end_customer_subscription_status_changed(
            session,
            billing_settings.tenant_id,
            event["data"]["object"],
            notify_cancel=(event["type"] == "customer.subscription.deleted"),
        )
    elif event["type"] == _STATUS_EVENT_TYPE:
        billing_settings.stripe_account_status = await _resolve_account_status(
            event["data"]["object"]
        )
        await session.commit()
```

- [ ] **Step 11: Rodar os testes e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_stripe_connect_webhook.py -v`
Expected: todos passam.

- [ ] **Step 12: Rodar a suíte completa e ruff**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Expected: sem falhas novas.

- [ ] **Step 13: Commit**

```bash
cd apps/api
git add app/services/end_customer_billing.py app/api/v1/webhooks/stripe_connect.py tests/unit/test_end_customer_billing_service.py tests/unit/test_stripe_connect_webhook.py
git commit -m "feat(api): webhook trata ativação/renovação/cancelamento de assinatura do cliente final"
```

---

## Task 5: Worker — pula débito e o billing gate pra assinante ativo

**Files:**
- Modify: `apps/worker/app/tasks/inbound_context.py`
- Modify: `apps/worker/app/tasks/messages.py`
- Modify: `apps/worker/app/billing_gate.py`
- Test: `apps/worker/tests/unit/test_load_context.py`
- Test: `apps/worker/tests/unit/test_billing_gate.py`
- Test: `apps/worker/tests/unit/test_process_inbound_message.py`

**Interfaces:**
- Consumes: tabela `end_customer_subscriptions` (Task 1, espelhada em `apps/worker/app/tables.py`).
- Produces: `InboundContext.end_customer_has_active_subscription: bool`.

- [ ] **Step 1: Adicionar o campo a `InboundContext`**

Em `apps/worker/app/tasks/inbound_context.py`, adicionar depois de `end_customer_billing_exempt: bool = False` (último campo atual):

```python
    end_customer_has_active_subscription: bool = False
```

- [ ] **Step 2: Escrever o teste que falha (`_load_context`)**

Em `apps/worker/tests/unit/test_load_context.py`, a fixture `_session_with` (linhas 12-47 atuais) monta uma sequência FIXA de 9 resultados via `side_effect` — adicionar um 10º resultado (a nova query de assinatura ativa) exige um parâmetro novo na fixture e atualizar TODAS as chamadas existentes de `_session_with(...)` no arquivo (ler o arquivo inteiro antes de editar — são várias chamadas, cada uma precisa do argumento novo).

Adicionar o parâmetro `active_subscription=None` a `_session_with`:

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
    active_subscription=None,
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
            _result(scalar=active_subscription),
        ]
    )
    return session
```

Todas as chamadas existentes de `_session_with(...)` no arquivo continuam funcionando sem alteração (o parâmetro novo tem default `None`, que produz `active_subscription is not None` → `False` — mesmo comportamento de "sem assinatura" que já é o caso de toda a suíte hoje). Adicionar 1 teste novo:

```python
async def test_com_assinatura_ativa_marca_end_customer_has_active_subscription() -> None:
    session = _session_with(
        conversation=_conversation(),
        content="oi",
        number=_number(),
        credit_balance=Decimal(1000),
        billing_settings=SimpleNamespace(enabled=True, billing_gate_welcome_text=None),
        balance=Decimal(0),
        packages=[],
        active_subscription=uuid.uuid4(),
    )

    inbound = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert inbound.end_customer_has_active_subscription is True


async def test_billing_habilitado_sem_assinatura_marca_false() -> None:
    session = _session_with(
        conversation=_conversation(),
        content="oi",
        number=_number(),
        credit_balance=Decimal(1000),
        billing_settings=SimpleNamespace(enabled=True, billing_gate_welcome_text=None),
        balance=Decimal(0),
        packages=[],
        active_subscription=None,
    )

    inbound = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert inbound.end_customer_has_active_subscription is False
```

- [ ] **Step 3: Rodar os testes e confirmar que falham**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_load_context.py -v`
Expected: os 2 testes novos falham com `AttributeError: 'InboundContext' object has no attribute 'end_customer_has_active_subscription'` (o campo já existe no dataclass desde o Step 1, mas `_load_context` nunca o popula — o default `False` do dataclass mascararia o teste "sem assinatura", então a falha real está no teste "com assinatura ativa" esperando `True` e recebendo `False`; confirmar as duas falhas ao rodar, não só uma).

- [ ] **Step 4: Implementar a query em `_load_context`**

Em `apps/worker/app/tasks/messages.py`, adicionar `or_` e `func` ao import de `sqlalchemy` (linha 8 atual):

```python
from sqlalchemy import func, insert, or_, select, update
```

E, dentro do bloco `if end_customer_billing_enabled:` de `_load_context` (depois da query de `packages_result`, antes do `return InboundContext(...)`):

```python
        active_subscription = await session.scalar(
            select(tables.end_customer_subscriptions.c.id).where(
                tables.end_customer_subscriptions.c.tenant_id == uuid.UUID(tenant_id),
                tables.end_customer_subscriptions.c.contact_phone_number
                == conversation.contact_phone_number,
                tables.end_customer_subscriptions.c.status == "active",
                or_(
                    tables.end_customer_subscriptions.c.current_period_end.is_(None),
                    tables.end_customer_subscriptions.c.current_period_end >= func.now(),
                ),
            )
        )
```

(inicializar `active_subscription = None` antes do `if end_customer_billing_enabled:`, mesmo padrão já usado por `end_customer_balance`/`end_customer_packages` — a query só roda quando a cobrança está habilitada.)

E adicionar o campo ao `return InboundContext(...)` final:

```python
        end_customer_has_active_subscription=active_subscription is not None,
```

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_load_context.py -v`
Expected: todos passam.

- [ ] **Step 6: Escrever o teste que falha (`maybe_enter_gate`)**

Em `apps/worker/tests/unit/test_billing_gate.py`, adicionar à classe `TestMaybeEnterGate`:

```python
    async def test_nao_entra_no_gate_com_assinatura_ativa(self) -> None:
        session = AsyncMock()
        inbound = _inbound(
            end_customer_balance=Decimal(0), end_customer_has_active_subscription=True
        )

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is False
```

- [ ] **Step 7: Rodar o teste e confirmar que falha**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_billing_gate.py -v -k assinatura`
Expected: falha — `entered` é `True` hoje (a condição de entrada não considera assinatura).

- [ ] **Step 8: Implementar a checagem em `maybe_enter_gate`**

Em `apps/worker/app/billing_gate.py`, modificar a condição de entrada (linhas 38-43 atuais):

```python
    if (
        inbound.conversation_state == "agent"
        and inbound.end_customer_billing_enabled
        and not inbound.end_customer_billing_exempt
        and not inbound.end_customer_has_active_subscription
        and inbound.end_customer_balance <= 0
    ):
```

- [ ] **Step 9: Rodar o teste e confirmar que passa**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_billing_gate.py -v`
Expected: todos passam.

- [ ] **Step 10: Escrever os testes que falham (`process_inbound_message` — silêncio e débito)**

Ler `apps/worker/tests/unit/test_process_inbound_message.py` por completo primeiro, pra identificar o helper existente de construção de `InboundContext`/mocks usado nos testes de billing já existentes nesse arquivo (mencionado no relatório da Task 3 da migração Connect como `_inbound_com_billing` ou nome equivalente) — seguir exatamente esse padrão, não inventar um novo. Adicionar:

```python
async def test_assinante_ativo_nao_debita_tenant_nem_cliente_final(...):
    # usar o mesmo padrão de mocks/fixtures já estabelecido no arquivo pros
    # testes de "customer_funded"/"tenant zerado" — variar só
    # end_customer_has_active_subscription=True e credit_balance=0 (tenant
    # também zerado, pra provar que mesmo assim NÃO fica em silêncio e NÃO
    # debita nenhum dos dois lados)
    ...
    # asserções: send_message_to_agents foi chamado (não ficou em silêncio);
    # nem _debitar_creditos nem _debitar_creditos_cliente_final foram chamados
```

(Este passo depende da leitura do arquivo real — o corpo exato do teste, incluindo quais funções mockar e como, deve seguir o padrão já estabelecido nos testes vizinhos de billing nesse mesmo arquivo. Escrever o teste seguindo esse padrão antes de prosseguir pro Step 11.)

- [ ] **Step 11: Rodar o teste e confirmar que falha**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_process_inbound_message.py -v -k assinante`
Expected: falha — hoje, com `credit_balance<=0` e sem saldo de cliente final positivo, a mensagem cai no bloqueio de silêncio (não devia, pra assinante ativo).

- [ ] **Step 12: Implementar em `process_inbound_message`**

Em `apps/worker/app/tasks/messages.py`, modificar a checagem de silêncio (linhas 175-183 atuais):

```python
    if (
        inbound.credit_balance <= 0
        and not customer_funded
        and not inbound.end_customer_has_active_subscription
    ):
```

E modificar o bloco de débito (linhas 257-284 atuais):

```python
        if credits and first_message_id is not None:
            if inbound.end_customer_has_active_subscription:
                # Ilimitado: nenhum dos dois lados é debitado enquanto a
                # assinatura estiver ativa — o tenant absorve o custo do LLM,
                # sem teto automático nesta v1 (ver design doc).
                pass
            elif customer_funded:
                await _debitar_creditos_cliente_final(
                    session,
                    tenant_id,
                    inbound.contact_phone_number,
                    first_message_id,
                    tokens_used,
                    credits,
                    tokens_input,
                    tokens_output,
                    config.id,
                )
            else:
                await _debitar_creditos(
                    session,
                    tenant_id,
                    first_message_id,
                    tokens_used,
                    credits,
                    tokens_input,
                    tokens_output,
                    config.id,
                )
```

- [ ] **Step 13: Rodar o teste e confirmar que passa**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_process_inbound_message.py -v`
Expected: todos passam.

- [ ] **Step 14: Rodar a suíte completa do `apps/worker` e ruff**

Run: `cd apps/worker && python3 -m pytest tests/unit -q && python3 -m ruff check .`
Expected: sem falhas novas. ⚠️ Nunca `uv run` neste app (venv quebrado) — sempre `python3 -m`.

- [ ] **Step 15: Commit**

```bash
cd apps/worker
git add app/tasks/inbound_context.py app/tasks/messages.py app/billing_gate.py tests/unit/test_load_context.py tests/unit/test_billing_gate.py tests/unit/test_process_inbound_message.py
git commit -m "feat(worker): pula billing gate e débito dos dois lados pra assinante ativo"
```

---

## Task 6: WhatsApp — 2 seções na lista quando há pacote de assinatura

**Files:**
- Modify: `apps/worker/app/tasks/messages.py`
- Modify: `apps/worker/app/billing_gate.py`
- Test: `apps/worker/tests/unit/test_load_context.py`
- Test: `apps/worker/tests/unit/test_billing_gate.py`

**Interfaces:**
- Consumes: `EndCustomerCreditPackage.kind` (Task 1), propagado em `end_customer_packages: list[dict]` (já existe em `InboundContext`, ganha a chave `"kind"` em cada dict).
- Produces: `_packages_to_sections(packages: list[dict]) -> list[dict]` passa a devolver 2 seções quando existe ao menos 1 pacote `kind="subscription"` na lista; comportamento idêntico ao atual (1 seção) quando não há nenhum.

- [ ] **Step 1: Escrever o teste que falha (`_load_context` propaga `kind`)**

Em `apps/worker/tests/unit/test_load_context.py`, localizar o teste que já verifica o conteúdo de `end_customer_packages` (existe algum — a query de `packages_result` já é testada hoje) e adicionar uma asserção nova de que o dict de cada pacote inclui `"kind"`. Se não existir um teste dedicado a essa lista, adicionar:

```python
async def test_pacotes_incluem_kind() -> None:
    session = _session_with(
        conversation=_conversation(),
        content="oi",
        number=_number(),
        credit_balance=Decimal(1000),
        billing_settings=SimpleNamespace(enabled=True, billing_gate_welcome_text=None),
        balance=Decimal(0),
        packages=[
            SimpleNamespace(
                id=uuid.uuid4(), name="Básico", price_brl=Decimal("49.90"),
                credits_granted=500, kind="one_time",
            )
        ],
    )

    inbound = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert inbound.end_customer_packages[0]["kind"] == "one_time"
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_load_context.py -v -k kind`
Expected: falha — `KeyError: 'kind'` (a query e o dict de `end_customer_packages` não incluem essa coluna ainda).

- [ ] **Step 3: Adicionar `kind` à query e ao dict de pacotes**

Em `apps/worker/app/tasks/messages.py`, modificar a query `packages_result` dentro de `_load_context` (linhas 368-378 atuais):

```python
        packages_result = await session.execute(
            select(
                tables.end_customer_credit_packages.c.id,
                tables.end_customer_credit_packages.c.name,
                tables.end_customer_credit_packages.c.price_brl,
                tables.end_customer_credit_packages.c.kind,
                tables.end_customer_credit_packages.c.credits_granted,
            ).where(
                tables.end_customer_credit_packages.c.tenant_id == uuid.UUID(tenant_id),
                tables.end_customer_credit_packages.c.active.is_(True),
            )
        )
        end_customer_packages = [
            {
                "id": str(row.id),
                "name": row.name,
                "price_brl": str(row.price_brl),
                "kind": row.kind,
                "credits_granted": row.credits_granted,
            }
            for row in packages_result
        ]
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_load_context.py -v`
Expected: todos passam.

- [ ] **Step 5: Escrever o teste que falha (`_packages_to_sections`)**

Em `apps/worker/tests/unit/test_billing_gate.py`, importar `_packages_to_sections` (é privada, mas já é importável direto do módulo, mesmo padrão de outras funções privadas testadas neste arquivo — conferir se já há um teste direto dessa função; se não houver, adicionar um bloco novo):

```python
from app.billing_gate import _packages_to_sections


class TestPackagesToSections:
    def test_sem_assinatura_mantem_1_secao(self) -> None:
        packages = [
            {"id": "p1", "name": "Básico", "price_brl": "49.90", "kind": "one_time", "credits_granted": 500},
        ]

        sections = _packages_to_sections(packages)

        assert len(sections) == 1
        assert sections[0]["title"] == "Pacotes disponíveis"
        assert sections[0]["rows"][0]["description"] == "R$ 49.90 = 500 créditos"

    def test_com_assinatura_gera_2_secoes(self) -> None:
        packages = [
            {"id": "p1", "name": "Básico", "price_brl": "49.90", "kind": "one_time", "credits_granted": 500},
            {"id": "p2", "name": "Ilimitado", "price_brl": "99.90", "kind": "subscription", "credits_granted": None},
        ]

        sections = _packages_to_sections(packages)

        assert len(sections) == 2
        assert sections[0]["title"] == "Pacotes de créditos"
        assert sections[0]["rows"][0]["description"] == "R$ 49.90 = 500 créditos"
        assert sections[1]["title"] == "Assinatura mensal"
        assert sections[1]["rows"][0]["description"] == "R$ 99.90/mês — conversas ilimitadas"

    def test_so_assinatura_sem_pacote_avulso(self) -> None:
        packages = [
            {"id": "p2", "name": "Ilimitado", "price_brl": "99.90", "kind": "subscription", "credits_granted": None},
        ]

        sections = _packages_to_sections(packages)

        assert len(sections) == 1
        assert sections[0]["title"] == "Assinatura mensal"
```

- [ ] **Step 6: Rodar os testes e confirmar que falham**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_billing_gate.py -v -k Sections`
Expected: `test_com_assinatura_gera_2_secoes` e `test_so_assinatura_sem_pacote_avulso` falham (hoje sempre 1 seção, título fixo "Pacotes disponíveis"); `test_sem_assinatura_mantem_1_secao` já passa (comportamento idêntico ao atual) — confirmar que passa antes de seguir.

- [ ] **Step 7: Implementar `_packages_to_sections`**

Em `apps/worker/app/billing_gate.py`, substituir a função (linhas 86-99 atuais):

```python
def _package_row(package: dict) -> dict:
    if package.get("kind") == "subscription":
        description = f"R$ {package['price_brl']}/mês — conversas ilimitadas"
    else:
        description = f"R$ {package['price_brl']} = {package['credits_granted']} créditos"
    return {"id": package["name"], "title": package["name"], "description": description}


def _packages_to_sections(packages: list[dict]) -> list[dict]:
    # .get("kind", "one_time") — compatibilidade com qualquer chamador que
    # ainda não propague "kind" (nenhum hoje, mas evita um KeyError silencioso
    # se um teste/fixture mais antigo não tiver esse campo).
    avulsos = [p for p in packages if p.get("kind", "one_time") != "subscription"]
    assinaturas = [p for p in packages if p.get("kind") == "subscription"]

    if not assinaturas:
        return [{"title": "Pacotes disponíveis", "rows": [_package_row(p) for p in avulsos]}]

    sections = []
    if avulsos:
        sections.append({"title": "Pacotes de créditos", "rows": [_package_row(p) for p in avulsos]})
    sections.append({"title": "Assinatura mensal", "rows": [_package_row(p) for p in assinaturas]})
    return sections
```

- [ ] **Step 8: Rodar os testes e confirmar que passam**

Run: `cd apps/worker && python3 -m pytest tests/unit/test_billing_gate.py -v`
Expected: todos passam (a função `_resolve_package_by_title`, que resolve a seleção do cliente por título, continua funcionando sem alteração — ela busca em `inbound.end_customer_packages` inteiro, sem depender de seção).

- [ ] **Step 9: Rodar a suíte completa do `apps/worker` e ruff**

Run: `cd apps/worker && python3 -m pytest tests/unit -q && python3 -m ruff check .`
Expected: sem falhas novas.

- [ ] **Step 10: Commit**

```bash
cd apps/worker
git add app/tasks/messages.py app/billing_gate.py tests/unit/test_load_context.py tests/unit/test_billing_gate.py
git commit -m "feat(worker): lista de pacotes do WhatsApp ganha seção de assinatura mensal"
```

---

## Task 7: Painel — seletor de tipo de pacote

**Files:**
- Modify: `apps/web/src/components/EndCustomerBillingPanel.tsx`
- Test: `apps/web/__tests__/EndCustomerBillingPanel.test.tsx`

**Interfaces:**
- Consumes: `settings.billing_provider` (já existe no componente), `Package.kind`/`credits_granted` novos.
- Produces: formulário de criação de pacote ganha seletor Avulso/Assinatura mensal (só visível quando `billing_provider === "connect"`); campo "Créditos" some quando "Assinatura mensal" selecionado; listagem ganha badge Avulso/Mensal por pacote.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar a `apps/web/__tests__/EndCustomerBillingPanel.test.tsx`:

```tsx
it("mostra o seletor de tipo de pacote quando billing_provider é connect", async () => {
  mockLoad({
    enabled: false,
    billing_mode: "credits",
    billing_provider: "connect",
    stripe_account_id: "acct_123",
    stripe_account_status: "active",
    stripe_secret_key_configured: false,
    stripe_webhook_secret_configured: false,
    end_customer_tokens_per_credit: null,
  });

  render(<EndCustomerBillingPanel />);

  await waitFor(() => expect(screen.getByLabelText(/tipo de pacote/i)).toBeInTheDocument());
});

it("não mostra o seletor de tipo de pacote quando billing_provider é standalone (grandfathered)", async () => {
  mockLoad({
    enabled: true,
    billing_mode: "credits",
    billing_provider: "standalone",
    stripe_account_id: null,
    stripe_account_status: null,
    stripe_secret_key_configured: true,
    stripe_webhook_secret_configured: true,
    end_customer_tokens_per_credit: null,
  });

  render(<EndCustomerBillingPanel />);

  await waitFor(() => expect(screen.getByLabelText(/nome do pacote/i)).toBeInTheDocument());
  expect(screen.queryByLabelText(/tipo de pacote/i)).not.toBeInTheDocument();
});

it("esconde o campo créditos e envia kind=subscription quando assinatura mensal é escolhida", async () => {
  mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "end-customer-billing/packages" && init?.method === "POST") {
      return {
        ok: true,
        json: async () => ({ id: "p-3", name: "Ilimitado", price_brl: "99.90", kind: "subscription", credits_granted: null, active: true }),
      };
    }
    if (path === "end-customer-billing/settings") {
      return {
        ok: true,
        json: async () => ({
          enabled: false, billing_mode: "credits", billing_provider: "connect",
          stripe_account_id: "acct_123", stripe_account_status: "active",
          stripe_secret_key_configured: false, stripe_webhook_secret_configured: false,
          end_customer_tokens_per_credit: null,
        }),
      };
    }
    if (path === "end-customer-billing/packages") return { ok: true, json: async () => [] };
    return { ok: false, json: async () => null };
  });

  render(<EndCustomerBillingPanel />);
  await waitFor(() => expect(screen.getByLabelText(/tipo de pacote/i)).toBeInTheDocument());

  fireEvent.change(screen.getByLabelText(/tipo de pacote/i), { target: { value: "subscription" } });
  expect(screen.queryByLabelText(/créditos/i)).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText(/nome do pacote/i), { target: { value: "Ilimitado" } });
  fireEvent.change(screen.getByLabelText(/preço/i), { target: { value: "99.90" } });
  fireEvent.click(screen.getByRole("button", { name: /adicionar pacote/i }));

  await waitFor(() => expect(screen.getByText("Ilimitado")).toBeInTheDocument());
  const postCall = mockedFetch.mock.calls.find(
    ([path, init]) => path === "end-customer-billing/packages" && init?.method === "POST",
  );
  const body = JSON.parse(postCall![1].body as string);
  expect(body.kind).toBe("subscription");
  expect(body.credits_granted).toBeUndefined();
});

it("mostra badge Mensal/Avulso na listagem de pacotes", async () => {
  mockLoad(
    {
      enabled: true, billing_mode: "credits", billing_provider: "connect",
      stripe_account_id: "acct_123", stripe_account_status: "active",
      stripe_secret_key_configured: false, stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    },
    [
      { id: "p-1", name: "Básico", price_brl: "49.90", kind: "one_time", credits_granted: 500, active: true },
      { id: "p-2", name: "Ilimitado", price_brl: "99.90", kind: "subscription", credits_granted: null, active: true },
    ],
  );

  render(<EndCustomerBillingPanel />);

  await waitFor(() => expect(screen.getByText("Básico")).toBeInTheDocument());
  expect(screen.getByText("Avulso")).toBeInTheDocument();
  expect(screen.getByText("Mensal")).toBeInTheDocument();
});
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd apps/web && pnpm vitest run __tests__/EndCustomerBillingPanel.test.tsx -t "tipo de pacote|badge"`
Expected: falha em todos os 4 — nenhum seletor/badge existe ainda.

- [ ] **Step 3: Implementar no componente**

Em `apps/web/src/components/EndCustomerBillingPanel.tsx`:

Ampliar o tipo `Package` (linhas 37-43 atuais):

```tsx
type Package = {
  id: string;
  name: string;
  price_brl: string;
  kind: string;
  credits_granted: number | null;
  active: boolean;
};
```

Ampliar `EMPTY_PACKAGE_FORM` (linha 45 atual) e o estado do formulário pra incluir `kind`:

```tsx
const EMPTY_PACKAGE_FORM = { name: "", price_brl: "", credits_granted: "", kind: "one_time" };
```

Modificar `handleCreatePackage` (linhas 151-176 atuais) — o corpo do `POST` passa a omitir `credits_granted` quando `kind === "subscription"`:

```tsx
  async function handleCreatePackage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setCreatingPackage(true);
    try {
      const body: Record<string, unknown> = {
        name: packageForm.name,
        price_brl: packageForm.price_brl,
        kind: packageForm.kind,
      };
      if (packageForm.kind !== "subscription") {
        body.credits_granted = Number(packageForm.credits_granted);
      }
      const response = await backendFetch("end-customer-billing/packages", {
        method: "POST",
        body: JSON.stringify(body),
      });
      const responseBody = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(responseBody, "Falha ao criar pacote — tente novamente."));
        return;
      }
      setPackages([...packages, responseBody]);
      setPackageForm(EMPTY_PACKAGE_FORM);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setCreatingPackage(false);
    }
  }
```

No formulário de criação de pacote (JSX, dentro de `<form onSubmit={handleCreatePackage}>`, linhas 416-453 atuais), adicionar o seletor logo antes do campo "Nome do pacote" — só quando `billing_provider === "connect"` — e envolver o campo "Créditos" numa condição:

```tsx
        <form onSubmit={handleCreatePackage} className="mt-4 flex max-w-md flex-col gap-4">
          {settings.billing_provider === "connect" && (
            <label className="flex flex-col gap-1 text-sm text-ink">
              Tipo de pacote
              <select
                value={packageForm.kind}
                onChange={(event) => setPackageForm({ ...packageForm, kind: event.target.value })}
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              >
                <option value="one_time">Avulso (créditos)</option>
                <option value="subscription">Assinatura mensal (ilimitado)</option>
              </select>
            </label>
          )}
          <label className="flex flex-col gap-1 text-sm text-ink">
            Nome do pacote
            <input
              required
              value={packageForm.name}
              onChange={(event) => setPackageForm({ ...packageForm, name: event.target.value })}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-ink">
            Preço (R$)
            <input
              required
              value={packageForm.price_brl}
              onChange={(event) => setPackageForm({ ...packageForm, price_brl: event.target.value })}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          {packageForm.kind !== "subscription" && (
            <label className="flex flex-col gap-1 text-sm text-ink">
              Créditos
              <input
                required
                type="number"
                min={1}
                value={packageForm.credits_granted}
                onChange={(event) =>
                  setPackageForm({ ...packageForm, credits_granted: event.target.value })
                }
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
          )}
          <button
            type="submit"
            disabled={creatingPackage}
            className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
          >
            {creatingPackage ? "Adicionando..." : "Adicionar pacote"}
          </button>
        </form>
```

Na listagem de pacotes (JSX, linhas 393-414 atuais), adicionar o badge:

```tsx
        <ul className="mt-4 max-w-md">
          {packages.length === 0 && (
            <li className="py-4 text-sm text-muted">Nenhum pacote cadastrado ainda.</li>
          )}
          {packages.map((pkg) => (
            <li key={pkg.id} className="flex items-center justify-between border-b border-line py-3">
              <div>
                <p className="font-medium text-ink">
                  {pkg.name}{" "}
                  <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted">
                    {pkg.kind === "subscription" ? "Mensal" : "Avulso"}
                  </span>
                </p>
                <p className="text-xs text-muted">
                  {pkg.kind === "subscription"
                    ? `R$ ${pkg.price_brl}/mês`
                    : `R$ ${pkg.price_brl} · ${pkg.credits_granted} créditos`}
                </p>
              </div>
              <button
                type="button"
                onClick={() => void handleDeletePackage(pkg)}
                className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
              >
                Excluir
              </button>
            </li>
          ))}
        </ul>
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd apps/web && pnpm vitest run __tests__/EndCustomerBillingPanel.test.tsx`
Expected: todos passam, incluindo os 4 novos e todos os pré-existentes (as chamadas de `mockLoad`/mocks de pacotes existentes que não incluem `kind` explicitamente precisam continuar funcionando — se algum teste pré-existente quebrar por `pkg.kind` sendo `undefined`, adicionar `kind: "one_time"` ao objeto de pacote mockado desse teste especificamente, seguindo o mesmo princípio já usado nas tasks anteriores desta sessão pra manter fixtures explícitas em vez de depender de default implícito).

- [ ] **Step 5: Rodar a suíte completa e lint**

Run: `cd apps/web && pnpm test && pnpm lint`
Expected: sem falhas novas.

- [ ] **Step 6: Commit**

```bash
cd apps/web
git add src/components/EndCustomerBillingPanel.tsx __tests__/EndCustomerBillingPanel.test.tsx
git commit -m "feat(web): seletor de tipo de pacote (avulso/assinatura) em /configuracoes/cobranca-clientes"
```

---

## Task 8: Documentação (`CLAUDE.md`)

**Files:**
- Modify: `/home/falcao/development/advoxs/CLAUDE.md`

**Interfaces:**
- Consumes: nada de código — só reflete o que as Tasks 1-7 implementaram.

- [ ] **Step 1: Atualizar a subseção "Cobrança do cliente final" em `CLAUDE.md`**

Adicionar um parágrafo novo (depois do parágrafo sobre o modelo Connect já documentado) descrevendo: `kind` em `end_customer_credit_packages` (`"one_time"`/`"subscription"`, imutável, restrito a `billing_provider="connect"`), tabela `end_customer_subscriptions`, checkout com `mode="subscription"` + `price_data.recurring`, os 3 eventos novos tratados pelo webhook único de Connect (`checkout.session.completed` com `kind=end_customer_subscription`, `invoice.payment_succeeded` silencioso, `customer.subscription.deleted`/`.updated` com aviso só no cancelamento), a checagem no worker (`InboundContext.end_customer_has_active_subscription`, pula gate e débito dos dois lados), e a lista do WhatsApp com 2 seções. Referenciar a spec: `docs/superpowers/specs/2026-07-25-assinatura-recorrente-cliente-final-design.md`. Escrever o texto real (sem placeholder) refletindo o que as Tasks 1-7 de fato implementaram — usar a spec como referência de intenção, mas descrever o estado implementado (✅), verificando contra o código final (não contra este plano) exatamente como já foi feito na Task 7/documentação da migração Connect.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: documenta a assinatura mensal recorrente pro cliente final"
```
