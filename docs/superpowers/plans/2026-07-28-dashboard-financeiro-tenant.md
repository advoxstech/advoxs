# Dashboard financeiro do tenant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O tenant vê, filtrável por período, quanto faturou vendendo pros próprios clientes finais (por mês e por cliente, com gráfico) e quanto gastou comprando créditos da Advoxs no mesmo tipo de recorte.

**Architecture:** Nova tabela `end_customer_subscription_payments` grava o histórico de pagamento de cada assinatura (criação + renovação), espelhando o que compra avulsa já faz em `end_customer_credit_transactions` — o relatório de faturamento passa a ser 100% agregação do nosso próprio banco (2 tabelas, sem chamada à Stripe em tempo de leitura). O gasto do tenant é uma agregação simples sobre `credit_transactions`, já completo hoje. Gráfico: SVG feito à mão, mesmo padrão já usado em `NewTenantsChart.tsx`, sem nova dependência.

**Tech Stack:** `apps/api` (FastAPI + Python 3.12, uv, Alembic — migration mais recente é `0022`), `apps/web` (Next.js 15, Vitest).

## Global Constraints

- Spec de referência: `docs/superpowers/specs/2026-07-28-dashboard-financeiro-tenant-design.md`.
- Faturamento do cliente final só aparece pra tenants `billing_provider="connect"` — mesma restrição já aplicada à assinatura recorrente.
- Receita bruta só nesta v1 — sem desconto de reembolso.
- Relatório de faturamento nunca chama a Stripe em tempo de leitura — é 100% agregação do nosso banco.
- Toda tabela tenant-scoped nova precisa de RLS habilitada **na própria migration de criação** — um esquecimento aqui já foi encontrado e corrigido numa revisão anterior desta mesma sessão (migration `0022`), não repetir.
- 1 item desta entrega depende de um campo específico da API da Stripe (`invoice.status_transitions.paid_at`) e tem passo de "confirmar antes de codificar" na task correspondente (Task 2) — mesmo padrão já usado nas 2 features anteriores desta sessão pra qualquer dependência de formato de payload da Stripe.

---

## Task 1: Modelo de dados — `end_customer_subscription_payments`

**Files:**
- Create: `apps/api/alembic/versions/0023_historico_pagamento_assinatura.py`
- Modify: `apps/api/app/models/end_customer_billing.py`
- Modify: `apps/api/app/models/__init__.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_service.py` (só confirmação indireta — a tabela em si não tem rota própria, é testada pela Task 2)

**Interfaces:**
- Produces: classe `EndCustomerSubscriptionPayment` (`id`, `tenant_id`, `contact_phone_number`, `end_customer_subscription_id`, `amount_brl: Decimal`, `stripe_invoice_id: str` único, `paid_at: datetime`).

- [ ] **Step 1: Escrever a migration**

```python
"""Histórico de pagamento de assinatura mensal recorrente do cliente final —
1 linha por invoice pago (criação + cada renovação), espelhando o que compra
avulsa já faz em end_customer_credit_transactions. Alimenta o relatório de
faturamento — ver docs/superpowers/specs/2026-07-28-dashboard-financeiro-tenant-design.md.

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-28
"""

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = ["end_customer_subscription_payments"]


def upgrade() -> None:
    op.create_table(
        "end_customer_subscription_payments",
        sa.Column(
            "id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, index=True
        ),
        sa.Column("contact_phone_number", sa.String(), nullable=False),
        sa.Column(
            "end_customer_subscription_id",
            sa.Uuid(),
            sa.ForeignKey("end_customer_subscriptions.id"),
            nullable=False,
        ),
        sa.Column("amount_brl", sa.Numeric(10, 2), nullable=False),
        sa.Column("stripe_invoice_id", sa.String(), nullable=False, unique=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
    )

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("end_customer_subscription_payments")
```

- [ ] **Step 2: Rodar a migration e verificar RLS empiricamente**

Run: `cd apps/api && uv run alembic upgrade head`
Run: `docker exec advoxs-postgres-1 psql -U advoxs -d advoxs -c "SELECT relrowsecurity FROM pg_class WHERE relname = 'end_customer_subscription_payments';"` (ajustar nome do container/credenciais conforme o ambiente local disponível)
Expected: migration aplica sem erro; `relrowsecurity` retorna `t`.

- [ ] **Step 3: Adicionar o model**

Em `apps/api/app/models/end_customer_billing.py`, adicionar ao bloco de imports do topo (`DateTime` já importado, adicionar nada novo de import) e, depois da classe `EndCustomerSubscription` (final do arquivo):

```python
class EndCustomerSubscriptionPayment(Base):
    """1 linha por invoice pago de uma assinatura mensal recorrente
    (criação + cada renovação) — histórico de faturamento, equivalente pra
    assinatura do que `EndCustomerCreditTransaction` (type=purchase) já é
    pra compra avulsa. Nunca lido em tempo real pelo worker — só consumido
    pelo relatório de faturamento (apps/api/app/api/v1/end_customer_billing.py)."""

    __tablename__ = "end_customer_subscription_payments"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    contact_phone_number: Mapped[str] = mapped_column(String, nullable=False)
    end_customer_subscription_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("end_customer_subscriptions.id"), nullable=False
    )
    amount_brl: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    stripe_invoice_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 4: Registrar em `app/models/__init__.py`**

Adicionar `EndCustomerSubscriptionPayment` ao import de `app.models.end_customer_billing` e ao `__all__`, mesmo padrão das outras classes já importadas de lá.

- [ ] **Step 5: Rodar a suíte completa e ruff**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: sem falhas novas (nenhum teste ainda referencia a classe nova — isso é esperado, a Task 2 é quem exercita).

- [ ] **Step 6: Commit**

```bash
cd apps/api
git add alembic/versions/0023_historico_pagamento_assinatura.py app/models/end_customer_billing.py app/models/__init__.py
git commit -m "feat(api): tabela end_customer_subscription_payments (histórico de faturamento de assinatura)"
```

---

## Task 2: Webhook — registra o pagamento da assinatura no histórico

**Files:**
- Modify: `apps/api/app/services/end_customer_billing.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_service.py`

**Interfaces:**
- Consumes: `EndCustomerSubscriptionPayment` (Task 1).
- Produces: `process_end_customer_subscription_renewed` mantém a mesma assinatura; passa a gravar 1 linha em `EndCustomerSubscriptionPayment` por invoice pago (idempotente por `stripe_invoice_id`), além do que já fazia (atualizar `status`/`current_period_end`).

- [ ] **Step 1 — CONFIRMAR ANTES DE CODIFICAR: onde fica o timestamp real do pagamento no `Invoice`**

O rascunho deste plano assume que o momento real em que a Stripe processou o pagamento fica em `invoice["status_transitions"]["paid_at"]` (unix timestamp) — mais preciso que `datetime.now(UTC)` (que reflete quando NOSSO webhook processou, podendo ter atraso). Confirmar isso contra o SDK instalado (`stripe-python` 15.3.0, mesma versão já usada nas 2 pesquisas anteriores desta sessão sobre o objeto `Invoice`) ou a doc atual — este mesmo arquivo já teve 2 suposições erradas sobre o shape do `Invoice` corrigidas por revisão nesta sessão (`Invoice.subscription`, `Invoice.period_end`), então não pule esta confirmação. Se o campo real divergir (nome diferente, estrutura diferente, ausente em algum caso), ajustar `_extract_paid_at` (Step 4 abaixo) de acordo, com fallback pra `datetime.now(UTC)` quando o campo não estiver presente — documentar a fonte consultada no relatório da task.

- [ ] **Step 2: Escrever o teste que falha**

Ler `apps/api/tests/unit/test_end_customer_billing_service.py` por completo primeiro — localizar a classe `TestProcessEndCustomerSubscriptionRenewed` (ou nome equivalente já existente pra `process_end_customer_subscription_renewed`) e seguir exatamente o padrão de mock já estabelecido nela (`session.scalar` com `AsyncMock`, `SimpleNamespace` pra simular o `EndCustomerSubscription` retornado). Adicionar:

```python
    async def test_registra_pagamento_no_historico_de_faturamento(self, session, monkeypatch) -> None:
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            stripe_subscription_id="sub_123",
            status="past_due",
            current_period_end=None,
            end_customer_credit_package_id=PACKAGE_ID,
        )
        package = SimpleNamespace(id=PACKAGE_ID, price_brl=Decimal("49.90"))
        session.scalar = AsyncMock(side_effect=[subscription, None])
        session.get = AsyncMock(return_value=package)
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        invoice = {
            "id": "in_999",
            "subscription": "sub_123",
            "lines": {"data": [{"period": {"end": 1735689600}}]},
            "status_transitions": {"paid_at": 1735689500},
        }

        await process_end_customer_subscription_renewed(session, TENANT_ID, invoice)

        assert len(added) == 1
        payment = added[0]
        assert payment.tenant_id == TENANT_ID
        assert payment.contact_phone_number == CONTACT
        assert payment.end_customer_subscription_id == subscription.id
        assert payment.amount_brl == Decimal("49.90")
        assert payment.stripe_invoice_id == "in_999"
        assert payment.paid_at == datetime.fromtimestamp(1735689500, UTC)

    async def test_invoice_duplicado_nao_registra_pagamento_2x(self, session, monkeypatch) -> None:
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            stripe_subscription_id="sub_123",
            status="active",
            current_period_end=None,
            end_customer_credit_package_id=PACKAGE_ID,
        )
        session.scalar = AsyncMock(side_effect=[subscription, uuid.uuid4()])
        session.add = MagicMock()
        invoice = {"id": "in_999", "subscription": "sub_123", "lines": {"data": []}}

        await process_end_customer_subscription_renewed(session, TENANT_ID, invoice)

        session.add.assert_not_called()

    async def test_assinatura_sem_pacote_nao_quebra_atualizacao_de_status(
        self, session, monkeypatch
    ) -> None:
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            stripe_subscription_id="sub_123",
            status="past_due",
            current_period_end=None,
            end_customer_credit_package_id=None,
        )
        session.scalar = AsyncMock(side_effect=[subscription, None])
        session.add = MagicMock()
        invoice = {"id": "in_999", "subscription": "sub_123", "lines": {"data": []}}

        await process_end_customer_subscription_renewed(session, TENANT_ID, invoice)

        assert subscription.status == "active"
        session.add.assert_not_called()
        session.commit.assert_awaited_once()
```

(Confirmar se `PACKAGE_ID`/`CONTACT`/`Decimal`/`datetime`/`UTC` já estão importados/definidos no topo do arquivo — usar os mesmos identificadores já existentes nas classes vizinhas, sem redefinir.)

- [ ] **Step 3: Rodar os testes e confirmar que falham**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py -v -k "historico_de_faturamento or duplicado_nao_registra or sem_pacote_nao_quebra"`
Expected: falha — `session.get`/`EndCustomerSubscriptionPayment` ainda não são usados por `process_end_customer_subscription_renewed`; `added` fica vazio quando deveria ter 1 item no primeiro teste.

- [ ] **Step 4: Implementar**

Em `apps/api/app/services/end_customer_billing.py`, adicionar `EndCustomerSubscriptionPayment` ao import de `app.models` (bloco já existente) e, depois de `_extract_period_end`, adicionar:

```python
def _extract_paid_at(invoice: dict) -> datetime:
    """Momento real em que a Stripe processou o pagamento — mais preciso
    que `datetime.now(UTC)` (quando NOSSO webhook processou, que pode
    atrasar). Fallback pro momento do processamento quando o campo não
    vem no payload."""
    paid_at_ts = (invoice.get("status_transitions") or {}).get("paid_at")
    if paid_at_ts is None:
        return datetime.now(UTC)
    return datetime.fromtimestamp(paid_at_ts, UTC)


async def _record_subscription_payment(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    subscription: EndCustomerSubscription,
    invoice: dict,
) -> None:
    """Grava 1 linha de histórico de faturamento por invoice pago —
    idempotente por stripe_invoice_id (webhook duplicado não duplica
    receita no relatório). Best-effort quanto ao valor: se o pacote da
    assinatura foi excluído (FK nullable), não há como saber o preço —
    loga e não grava, sem impedir a atualização de status/período que já
    aconteceu antes desta chamada."""
    invoice_id = invoice.get("id")
    if not invoice_id:
        return
    already_recorded = await session.scalar(
        select(EndCustomerSubscriptionPayment.id).where(
            EndCustomerSubscriptionPayment.stripe_invoice_id == invoice_id
        )
    )
    if already_recorded is not None:
        return

    if subscription.end_customer_credit_package_id is None:
        logger.warning(
            "Assinatura sem pacote associado, pagamento não registrado no "
            "histórico de faturamento | tenant=%s subscription=%s",
            tenant_id,
            subscription.stripe_subscription_id,
        )
        return
    package = await session.get(
        EndCustomerCreditPackage, subscription.end_customer_credit_package_id
    )
    if package is None:
        return

    session.add(
        EndCustomerSubscriptionPayment(
            tenant_id=tenant_id,
            contact_phone_number=subscription.contact_phone_number,
            end_customer_subscription_id=subscription.id,
            amount_brl=package.price_brl,
            stripe_invoice_id=invoice_id,
            paid_at=_extract_paid_at(invoice),
        )
    )
```

E modificar `process_end_customer_subscription_renewed` (linhas 484-513 atuais) — adicionar a chamada nova antes do `commit`:

```python
async def process_end_customer_subscription_renewed(
    session: AsyncSession, tenant_id: uuid.UUID, invoice: dict
) -> None:
    """Renovação mensal (`invoice.payment_succeeded`) — atualiza
    status/current_period_end, sem notificar o cliente (decisão deliberada:
    renovação silenciosa evita spam mensal). Também registra o pagamento em
    `end_customer_subscription_payments` (histórico de faturamento)."""
    invoice = _as_plain_dict(invoice)
    subscription_id = _extract_subscription_id(invoice)
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

    await _record_subscription_payment(session, tenant_id, subscription, invoice)

    await session.commit()
```

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py -v`
Expected: todos passam, incluindo os 3 novos.

- [ ] **Step 6: Rodar a suíte completa, ruff e ruff format**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: sem falhas novas.

- [ ] **Step 7: Commit**

```bash
cd apps/api
git add app/services/end_customer_billing.py tests/unit/test_end_customer_billing_service.py
git commit -m "feat(api): webhook de renovação de assinatura registra pagamento no histórico de faturamento"
```

---

## Task 3: Backend — `GET /end-customer-billing/revenue`

**Files:**
- Modify: `apps/api/app/services/end_customer_billing.py`
- Modify: `apps/api/app/schemas/end_customer_billing.py`
- Modify: `apps/api/app/api/v1/end_customer_billing.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_service.py`
- Test: `apps/api/tests/unit/test_end_customer_billing_packages_routes.py` (ou um arquivo de teste de rota dedicado — ver Step 6)

**Interfaces:**
- Consumes: `EndCustomerCreditTransaction`, `EndCustomerCreditPackage`, `EndCustomerSubscriptionPayment` (Task 1).
- Produces: `get_revenue_report(session, tenant_id, date_from, date_to) -> RevenueReportOut`. Rota `GET /api/v1/end-customer-billing/revenue?from=&to=` (mesmo padrão de parâmetros de `GET /conversations/usage`: `from_: date = Query(..., alias="from")`, `to: date = Query(...)`, 422 se `to < from_`). 404 se o tenant não é `billing_provider="connect"`.

- [ ] **Step 1: Escrever o teste que falha (serviço)**

Adicionar a `apps/api/tests/unit/test_end_customer_billing_service.py`:

```python
class TestGetRevenueReport:
    async def test_soma_compra_avulsa_e_pagamento_de_assinatura_por_mes_e_cliente(
        self, session
    ) -> None:
        purchase_rows = [
            (datetime(2026, 7, 1, tzinfo=UTC), "5511999998888", Decimal("49.90")),
            (datetime(2026, 7, 15, tzinfo=UTC), "5511999997777", Decimal("99.90")),
        ]
        subscription_rows = [
            (datetime(2026, 7, 10, tzinfo=UTC), "5511999998888", Decimal("29.90")),
        ]
        purchase_result = MagicMock()
        purchase_result.all.return_value = purchase_rows
        subscription_result = MagicMock()
        subscription_result.all.return_value = subscription_rows
        session.execute = AsyncMock(side_effect=[purchase_result, subscription_result])

        report = await get_revenue_report(
            session, TENANT_ID, date(2026, 7, 1), date(2026, 7, 31)
        )

        assert report.by_month == [RevenueByMonthOut(month="2026-07", total_brl=179.70)]
        assert report.by_customer[0] == RevenueByCustomerOut(
            contact_phone_number="5511999998888", total_brl=79.80
        )
        assert report.by_customer[1] == RevenueByCustomerOut(
            contact_phone_number="5511999997777", total_brl=99.90
        )

    async def test_sem_movimento_no_periodo_retorna_listas_vazias(self, session) -> None:
        empty_result = MagicMock()
        empty_result.all.return_value = []
        session.execute = AsyncMock(side_effect=[empty_result, empty_result])

        report = await get_revenue_report(
            session, TENANT_ID, date(2026, 7, 1), date(2026, 7, 31)
        )

        assert report.by_month == []
        assert report.by_customer == []
```

(Adicionar `from datetime import date, datetime, UTC` — conferir o que já está importado no topo do arquivo antes de duplicar; `date` provavelmente ainda não está importado, `datetime`/`UTC` já estão.)

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py -v -k RevenueReport`
Expected: `ImportError`/`NameError` — `get_revenue_report`/`RevenueByMonthOut`/`RevenueByCustomerOut` não existem ainda.

- [ ] **Step 3: Adicionar os schemas**

Em `apps/api/app/schemas/end_customer_billing.py`, adicionar ao final:

```python
class RevenueByMonthOut(BaseModel):
    month: str
    total_brl: float


class RevenueByCustomerOut(BaseModel):
    contact_phone_number: str
    total_brl: float


class RevenueReportOut(BaseModel):
    by_month: list[RevenueByMonthOut]
    by_customer: list[RevenueByCustomerOut]
```

- [ ] **Step 4: Implementar `get_revenue_report`**

Em `apps/api/app/services/end_customer_billing.py`, adicionar `func` e `EndCustomerSubscriptionPayment` aos imports existentes (`func` já está importado de `sqlalchemy`, confirmar antes de duplicar), e adicionar ao final do arquivo:

```python
async def get_revenue_report(
    session: AsyncSession, tenant_id: uuid.UUID, date_from: date, date_to: date
) -> RevenueReportOut:
    """Faturamento do cliente final (compra avulsa + pagamento de
    assinatura), agregado por mês e por cliente — 100% a partir do nosso
    próprio banco, nunca uma chamada à Stripe em tempo de leitura (ver
    docs/superpowers/specs/2026-07-28-dashboard-financeiro-tenant-design.md).
    Receita bruta só — sem desconto de reembolso nesta v1."""
    upper_bound = datetime.combine(date_to, time.max, tzinfo=UTC)
    lower_bound = datetime.combine(date_from, time.min, tzinfo=UTC)

    purchases = (
        await session.execute(
            select(
                func.date_trunc("month", EndCustomerCreditTransaction.created_at),
                EndCustomerCreditTransaction.contact_phone_number,
                EndCustomerCreditPackage.price_brl,
            )
            .join(
                EndCustomerCreditPackage,
                EndCustomerCreditPackage.id
                == EndCustomerCreditTransaction.end_customer_credit_package_id,
            )
            .where(
                EndCustomerCreditTransaction.tenant_id == tenant_id,
                EndCustomerCreditTransaction.type == "purchase",
                EndCustomerCreditTransaction.created_at >= lower_bound,
                EndCustomerCreditTransaction.created_at <= upper_bound,
            )
        )
    ).all()

    subscription_payments = (
        await session.execute(
            select(
                func.date_trunc("month", EndCustomerSubscriptionPayment.paid_at),
                EndCustomerSubscriptionPayment.contact_phone_number,
                EndCustomerSubscriptionPayment.amount_brl,
            ).where(
                EndCustomerSubscriptionPayment.tenant_id == tenant_id,
                EndCustomerSubscriptionPayment.paid_at >= lower_bound,
                EndCustomerSubscriptionPayment.paid_at <= upper_bound,
            )
        )
    ).all()

    by_month: dict[str, Decimal] = {}
    by_customer: dict[str, Decimal] = {}
    for month, contact, amount in [*purchases, *subscription_payments]:
        month_key = month.strftime("%Y-%m")
        by_month[month_key] = by_month.get(month_key, Decimal(0)) + amount
        by_customer[contact] = by_customer.get(contact, Decimal(0)) + amount

    return RevenueReportOut(
        by_month=[
            RevenueByMonthOut(month=month_key, total_brl=float(total))
            for month_key, total in sorted(by_month.items())
        ],
        by_customer=[
            RevenueByCustomerOut(contact_phone_number=contact, total_brl=float(total))
            for contact, total in sorted(by_customer.items(), key=lambda item: item[1], reverse=True)
        ],
    )
```

Adicionar `from datetime import date, time` ao import de `datetime` no topo do arquivo (já importa `UTC, datetime` — ampliar pra `date, datetime, time, UTC`), e importar `RevenueByCustomerOut, RevenueByMonthOut, RevenueReportOut` do módulo de schemas (bloco de import já existente de `app.schemas.end_customer_billing`).

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py -v -k RevenueReport`
Expected: 2 passed.

- [ ] **Step 6: Escrever o teste que falha (rota)**

Ler `apps/api/app/api/v1/end_customer_billing.py` por completo primeiro pra confirmar o padrão de `_get_settings_row`/checagem de `billing_provider` já usado em `connect_account_earnings` (rota mais recente, mesma restrição que esta task precisa). Criar `apps/api/tests/unit/test_end_customer_billing_revenue_routes.py`:

```python
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app
from app.schemas.end_customer_billing import RevenueByMonthOut, RevenueReportOut

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


def test_tenant_standalone_retorna_404(client, session):
    session.scalar.return_value = SimpleNamespace(billing_provider="standalone")

    response = client.get(
        "/api/v1/end-customer-billing/revenue?from=2026-07-01&to=2026-07-31"
    )

    assert response.status_code == 404


def test_tenant_connect_retorna_relatorio(client, session, monkeypatch):
    import app.api.v1.end_customer_billing as routes_module

    session.scalar.return_value = SimpleNamespace(billing_provider="connect")
    report = RevenueReportOut(by_month=[RevenueByMonthOut(month="2026-07", total_brl=179.70)], by_customer=[])
    get_revenue_report_mock = AsyncMock(return_value=report)
    monkeypatch.setattr(routes_module, "get_revenue_report", get_revenue_report_mock)

    response = client.get(
        "/api/v1/end-customer-billing/revenue?from=2026-07-01&to=2026-07-31"
    )

    assert response.status_code == 200
    assert response.json()["by_month"][0]["total_brl"] == 179.70
    get_revenue_report_mock.assert_awaited_once_with(
        session, TENANT_ID, date(2026, 7, 1), date(2026, 7, 31)
    )


def test_to_anterior_a_from_retorna_422(client, session):
    session.scalar.return_value = SimpleNamespace(billing_provider="connect")

    response = client.get(
        "/api/v1/end-customer-billing/revenue?from=2026-07-31&to=2026-07-01"
    )

    assert response.status_code == 422


def test_sem_token_retorna_401():
    response = TestClient(app).get(
        "/api/v1/end-customer-billing/revenue?from=2026-07-01&to=2026-07-31"
    )
    assert response.status_code == 401
```

- [ ] **Step 7: Rodar o teste e confirmar que falha**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_revenue_routes.py -v`
Expected: 404 — a rota `/revenue` não existe ainda.

- [ ] **Step 8: Implementar a rota**

Em `apps/api/app/api/v1/end_customer_billing.py`, adicionar aos imports: `date` de `datetime`; `RevenueReportOut` do módulo de schemas; `get_revenue_report` do módulo de serviço. Adicionar, depois de `connect_account_earnings`:

```python
@router.get("/revenue")
async def revenue_report(
    from_: date = Query(..., alias="from"),
    to: date = Query(...),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> RevenueReportOut:
    if to < from_:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'to' não pode ser anterior a 'from'",
        )
    row = await _get_settings_row(ctx, session)
    if row is None or row.billing_provider != "connect":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Faturamento disponível só pra tenants configurados via Stripe Connect",
        )
    return await get_revenue_report(session, ctx.tenant_id, from_, to)
```

Adicionar `Query` ao import de `fastapi` se ainda não estiver lá (conferir — outras rotas do mesmo arquivo já usam `Query` pra paginação, provavelmente já importado).

- [ ] **Step 9: Rodar os testes e confirmar que passam**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_revenue_routes.py -v`
Expected: todos passam.

- [ ] **Step 10: Rodar a suíte completa, ruff e ruff format**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: sem falhas novas.

- [ ] **Step 11: Commit**

```bash
cd apps/api
git add app/services/end_customer_billing.py app/schemas/end_customer_billing.py app/api/v1/end_customer_billing.py tests/unit/test_end_customer_billing_service.py tests/unit/test_end_customer_billing_revenue_routes.py
git commit -m "feat(api): endpoint de faturamento do cliente final por mês e por cliente"
```

---

## Task 4: Backend — `GET /billing/spending`

**Files:**
- Modify: `apps/api/app/services/billing.py`
- Modify: `apps/api/app/schemas/billing.py`
- Modify: `apps/api/app/api/v1/billing.py`
- Test: `apps/api/tests/unit/test_billing_service.py`
- Test: `apps/api/tests/unit/test_billing_routes.py` (confirmar nome exato do arquivo de teste de rota deste módulo antes de criar/editar — se não existir um arquivo dedicado às rotas de `/billing`, criar `test_billing_spending_routes.py`)

**Interfaces:**
- Consumes: `CreditTransaction`, `CreditPackage` (já existentes).
- Produces: `get_spending_report(session, tenant_id, date_from, date_to) -> SpendingReportOut` (`by_month: list[{month, total_brl}]`). Rota `GET /api/v1/billing/spending?from=&to=`, mesmo padrão de validação de `from`/`to` da Task 3.

- [ ] **Step 1: Escrever o teste que falha (serviço)**

Em `apps/api/tests/unit/test_billing_service.py`, adicionar (verificar imports necessários no topo — `date`, `func`, `MagicMock` já devem estar disponíveis ou precisam ser adicionados):

```python
class TestGetSpendingReport:
    async def test_soma_compras_por_mes(self, session) -> None:
        rows = [
            (datetime(2026, 7, 5, tzinfo=UTC), Decimal("100.00")),
            (datetime(2026, 7, 20, tzinfo=UTC), Decimal("250.00")),
        ]
        result = MagicMock()
        result.all.return_value = rows
        session.execute = AsyncMock(return_value=result)

        report = await get_spending_report(session, TENANT_ID, date(2026, 7, 1), date(2026, 7, 31))

        assert report.by_month == [SpendingByMonthOut(month="2026-07", total_brl=350.0)]

    async def test_sem_compra_no_periodo_retorna_lista_vazia(self, session) -> None:
        result = MagicMock()
        result.all.return_value = []
        session.execute = AsyncMock(return_value=result)

        report = await get_spending_report(session, TENANT_ID, date(2026, 7, 1), date(2026, 7, 31))

        assert report.by_month == []
```

(`TENANT_ID` já existe no topo do arquivo, `PACKAGE_ID`/`_package()` já existem — não precisa deste teste especificamente, mas confirmar `AsyncMock`/`MagicMock`/`Decimal`/`datetime`/`UTC` já importados; adicionar `from datetime import date` se ainda não estiver.)

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd apps/api && uv run pytest tests/unit/test_billing_service.py -v -k SpendingReport`
Expected: `ImportError`/`NameError` — `get_spending_report`/`SpendingByMonthOut` não existem ainda.

- [ ] **Step 3: Adicionar os schemas**

Em `apps/api/app/schemas/billing.py`, adicionar ao final:

```python
class SpendingByMonthOut(BaseModel):
    month: str
    total_brl: float


class SpendingReportOut(BaseModel):
    by_month: list[SpendingByMonthOut]
```

- [ ] **Step 4: Implementar `get_spending_report`**

Em `apps/api/app/services/billing.py`, adicionar `func`, `date`, `time` aos imports (`select` já importado de `sqlalchemy`; ampliar pra `func, select`; `datetime`/`Decimal` conferir se já importados — este arquivo hoje pode não importar `Decimal`/`date`/`time` diretamente, adicionar conforme necessário) e `SpendingByMonthOut, SpendingReportOut` do módulo de schemas. Adicionar ao final do arquivo:

```python
async def get_spending_report(
    session: AsyncSession, tenant_id: uuid.UUID, date_from: date, date_to: date
) -> SpendingReportOut:
    """Quanto o tenant gastou comprando créditos da Advoxs, agregado por mês.
    Mesma ressalva já documentada no CLAUDE.md pro dashboard de admin:
    price_brl reflete o preço do pacote no momento da consulta, não
    necessariamente o pago na época (a transação não guarda o preço pago)."""
    upper_bound = datetime.combine(date_to, time.max, tzinfo=UTC)
    lower_bound = datetime.combine(date_from, time.min, tzinfo=UTC)

    rows = (
        await session.execute(
            select(
                func.date_trunc("month", CreditTransaction.created_at),
                CreditPackage.price_brl,
            )
            .join(CreditPackage, CreditPackage.id == CreditTransaction.credit_package_id)
            .where(
                CreditTransaction.tenant_id == tenant_id,
                CreditTransaction.type == "purchase",
                CreditTransaction.created_at >= lower_bound,
                CreditTransaction.created_at <= upper_bound,
            )
        )
    ).all()

    by_month: dict[str, Decimal] = {}
    for month, price in rows:
        month_key = month.strftime("%Y-%m")
        by_month[month_key] = by_month.get(month_key, Decimal(0)) + price

    return SpendingReportOut(
        by_month=[
            SpendingByMonthOut(month=month_key, total_brl=float(total))
            for month_key, total in sorted(by_month.items())
        ]
    )
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `cd apps/api && uv run pytest tests/unit/test_billing_service.py -v -k SpendingReport`
Expected: 2 passed.

- [ ] **Step 6: Escrever o teste que falha (rota)**

Ler `apps/api/app/api/v1/billing.py` por completo (já lido — 4 rotas existentes: `/balance`, `/transactions`, `/checkout`, `/status`). Criar (ou estender, se já existir um arquivo de teste de rotas pra este módulo — verificar antes) o teste da rota nova, seguindo o padrão de `get_current_tenant`/`get_tenant_session` já usado nas rotas vizinhas deste mesmo router:

```python
def test_spending_com_to_anterior_a_from_retorna_422(client, session):
    response = client.get("/api/v1/billing/spending?from=2026-07-31&to=2026-07-01")

    assert response.status_code == 422


def test_spending_retorna_relatorio(client, session, monkeypatch):
    import app.api.v1.billing as routes_module
    from app.schemas.billing import SpendingByMonthOut, SpendingReportOut

    report = SpendingReportOut(by_month=[SpendingByMonthOut(month="2026-07", total_brl=350.0)])
    get_spending_report_mock = AsyncMock(return_value=report)
    monkeypatch.setattr(routes_module, "get_spending_report", get_spending_report_mock)

    response = client.get("/api/v1/billing/spending?from=2026-07-01&to=2026-07-31")

    assert response.status_code == 200
    assert response.json()["by_month"][0]["total_brl"] == 350.0
```

(Se não existir ainda um `client`/`session` fixture pras rotas de `/billing` num arquivo de teste dedicado, criar `apps/api/tests/unit/test_billing_spending_routes.py` com o mesmo padrão de fixture já usado em `test_end_customer_billing_revenue_routes.py` da Task 3, adaptando `get_current_tenant`/`get_tenant_session` pra este router.)

- [ ] **Step 7: Rodar o teste e confirmar que falha**

Run: `cd apps/api && uv run pytest -k spending -v`
Expected: 404 — rota não existe ainda.

- [ ] **Step 8: Implementar a rota**

Em `apps/api/app/api/v1/billing.py`, adicionar `date` ao import de `datetime`, `SpendingReportOut` ao import de schemas, `get_spending_report` ao import de `app.services.billing`. Adicionar ao final do arquivo:

```python
@router.get("/spending")
async def spending_report(
    from_: date = Query(..., alias="from"),
    to: date = Query(...),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> SpendingReportOut:
    if to < from_:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'to' não pode ser anterior a 'from'",
        )
    return await get_spending_report(session, ctx.tenant_id, from_, to)
```

- [ ] **Step 9: Rodar os testes e confirmar que passam**

Run: `cd apps/api && uv run pytest -k spending -v`
Expected: todos passam.

- [ ] **Step 10: Rodar a suíte completa, ruff e ruff format**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: sem falhas novas.

- [ ] **Step 11: Commit**

```bash
cd apps/api
git add app/services/billing.py app/schemas/billing.py app/api/v1/billing.py tests/unit/test_billing_service.py tests/unit/test_billing_spending_routes.py
git commit -m "feat(api): endpoint de gasto do tenant com créditos da Advoxs por mês"
```

---

## Task 5: Frontend — `formatBRL` compartilhado + componente de gráfico mensal

**Files:**
- Modify: `apps/web/src/lib/format.ts`
- Modify: `apps/web/src/components/ConnectEarnings.tsx`
- Create: `apps/web/src/components/MonthlyBarChart.tsx`
- Test: `apps/web/__tests__/format.test.ts`
- Test: `apps/web/__tests__/MonthlyBarChart.test.tsx`

**Interfaces:**
- Produces: `formatBRL(value: number): string` (exportado de `@/lib/format`). `MonthlyBarChart({data}: {data: {month: string, total_brl: number}[]})` — componente reutilizável pelas Tasks 6 e 7.

- [ ] **Step 1: Escrever o teste que falha (`formatBRL`)**

Ler `apps/web/__tests__/format.test.ts` por completo primeiro pra seguir o estilo exato dos testes já existentes de `formatCredits`/`formatPhone`. Adicionar:

```ts
describe("formatBRL", () => {
  it("formata com vírgula decimal e 2 casas, padrão pt-BR", () => {
    expect(formatBRL(49.9)).toBe("49,90");
    expect(formatBRL(1234.5)).toBe("1.234,50");
    expect(formatBRL(0)).toBe("0,00");
  });
});
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd apps/web && pnpm vitest run __tests__/format.test.ts -t formatBRL`
Expected: falha — `formatBRL` não existe ainda em `@/lib/format`.

- [ ] **Step 3: Implementar `formatBRL`**

Em `apps/web/src/lib/format.ts`, adicionar ao final (extraindo a mesma lógica hoje duplicada dentro de `ConnectEarnings.tsx`):

```ts
/** Valor em reais — pt-BR, sempre 2 casas decimais. */
export function formatBRL(value: number): string {
  return value.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
```

- [ ] **Step 4: Rodar o teste e confirmar que passa; remover a duplicação em `ConnectEarnings.tsx`**

Run: `cd apps/web && pnpm vitest run __tests__/format.test.ts -t formatBRL`
Expected: passa.

Em `apps/web/src/components/ConnectEarnings.tsx`: remover a função local `formatBRL` (linhas 27-29 atuais) e importar de `@/lib/format` (`import { formatBRL } from "@/lib/format";`, junto do import de `backendFetch`). Rodar `cd apps/web && pnpm vitest run __tests__/ConnectEarnings.test.tsx` pra confirmar que o teste existente desse componente continua passando sem alteração (só trocou a origem da função, comportamento idêntico).

- [ ] **Step 5: Escrever o teste que falha (`MonthlyBarChart`)**

Ler `apps/web/src/components/NewTenantsChart.tsx` por completo primeiro (já mapeado — SVG à mão, `WIDTH`/`HEIGHT`/`PADDING` fixos, hover com tooltip). Criar `apps/web/__tests__/MonthlyBarChart.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MonthlyBarChart } from "@/components/MonthlyBarChart";

describe("MonthlyBarChart", () => {
  it("mostra mensagem quando não há dados", () => {
    render(<MonthlyBarChart data={[]} />);

    expect(screen.getByText(/nenhum valor no período/i)).toBeInTheDocument();
  });

  it("renderiza 1 barra por mês", () => {
    const { container } = render(
      <MonthlyBarChart
        data={[
          { month: "2026-06", total_brl: 100 },
          { month: "2026-07", total_brl: 250 },
        ]}
      />,
    );

    expect(container.querySelectorAll("rect[data-bar]")).toHaveLength(2);
  });
});
```

- [ ] **Step 6: Rodar o teste e confirmar que falha**

Run: `cd apps/web && pnpm vitest run __tests__/MonthlyBarChart.test.tsx`
Expected: falha — módulo `@/components/MonthlyBarChart` não existe.

- [ ] **Step 7: Implementar `MonthlyBarChart`**

Criar `apps/web/src/components/MonthlyBarChart.tsx`:

```tsx
"use client";

import { useState } from "react";

import { formatBRL } from "@/lib/format";

type DataPoint = { month: string; total_brl: number };

const WIDTH = 600;
const HEIGHT = 160;
const PADDING = 24;

function monthLabel(month: string): string {
  const [year, monthNumber] = month.split("-");
  const date = new Date(Number(year), Number(monthNumber) - 1, 1);
  return date.toLocaleDateString("pt-BR", { month: "short", year: "2-digit" });
}

export function MonthlyBarChart({ data }: { data: DataPoint[] }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  if (data.length === 0) {
    return <p className="text-sm text-muted">Nenhum valor no período selecionado.</p>;
  }

  const maxTotal = Math.max(...data.map((d) => d.total_brl), 1);
  const innerWidth = WIDTH - PADDING * 2;
  const barWidth = innerWidth / data.length;
  const barGap = barWidth * 0.2;

  const bars = data.map((d, i) => {
    const barHeight = (d.total_brl / maxTotal) * (HEIGHT - PADDING * 2);
    return {
      x: PADDING + i * barWidth + barGap / 2,
      y: HEIGHT - PADDING - barHeight,
      width: barWidth - barGap,
      height: barHeight,
      ...d,
    };
  });

  const hovered = hoverIndex !== null ? bars[hoverIndex] : null;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full"
        onMouseLeave={() => setHoverIndex(null)}
      >
        {bars.map((bar, i) => (
          <g key={bar.month}>
            <rect
              data-bar
              x={bar.x}
              y={bar.y}
              width={bar.width}
              height={Math.max(bar.height, 1)}
              fill={hoverIndex === i ? "var(--accent)" : "var(--accent-soft)"}
              onMouseEnter={() => setHoverIndex(i)}
            />
            <text
              x={bar.x + bar.width / 2}
              y={HEIGHT - PADDING + 14}
              textAnchor="middle"
              className="fill-muted text-[10px]"
            >
              {monthLabel(bar.month)}
            </text>
          </g>
        ))}
      </svg>
      {hovered && (
        <div
          className="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded-sm border border-line bg-ground px-2 py-1 text-xs text-ink shadow-sm"
          style={{
            left: `${((hovered.x + hovered.width / 2) / WIDTH) * 100}%`,
            top: `${(hovered.y / HEIGHT) * 100}%`,
          }}
        >
          {monthLabel(hovered.month)}: R$ {formatBRL(hovered.total_brl)}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 8: Rodar o teste e confirmar que passa**

Run: `cd apps/web && pnpm vitest run __tests__/MonthlyBarChart.test.tsx`
Expected: 2 passed.

- [ ] **Step 9: Rodar a suíte completa e lint**

Run: `cd apps/web && pnpm test && pnpm lint`
Expected: sem falhas novas.

- [ ] **Step 10: Commit**

```bash
cd apps/web
git add src/lib/format.ts src/components/ConnectEarnings.tsx src/components/MonthlyBarChart.tsx __tests__/format.test.ts __tests__/MonthlyBarChart.test.tsx
git commit -m "feat(web): formatBRL compartilhado + componente de gráfico de barras mensal"
```

---

## Task 6: Frontend — aba "Faturamento"

**Files:**
- Create: `apps/web/src/components/RevenueReport.tsx`
- Modify: `apps/web/src/components/EndCustomerBillingTabs.tsx`
- Test: `apps/web/__tests__/RevenueReport.test.tsx`
- Test: `apps/web/__tests__/EndCustomerBillingTabs.test.tsx`

**Interfaces:**
- Consumes: `GET end-customer-billing/revenue?from=&to=` (Task 3), `MonthlyBarChart` (Task 5).
- Produces: `RevenueReport` — componente completo (filtro de período + gráfico + tabela por cliente).

- [ ] **Step 1: Escrever o teste que falha**

Ler `apps/web/src/components/ConversationsUsageReport.tsx` por completo primeiro (já mapeado — presets 7/30/90/personalizado, `rangeForPreset`, `isoDate`). Criar `apps/web/__tests__/RevenueReport.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RevenueReport } from "@/components/RevenueReport";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("RevenueReport", () => {
  it("carrega e mostra o total por cliente", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        by_month: [{ month: "2026-07", total_brl: 179.7 }],
        by_customer: [
          { contact_phone_number: "5511999998888", total_brl: 79.8 },
          { contact_phone_number: "5511999997777", total_brl: 99.9 },
        ],
      }),
    });

    render(<RevenueReport />);

    await waitFor(() => expect(screen.getByText(/79,80/)).toBeInTheDocument());
    expect(screen.getByText(/99,90/)).toBeInTheDocument();
  });

  it("muda o período ao clicar num preset", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ by_month: [], by_customer: [] }),
    });

    render(<RevenueReport />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());

    mockedFetch.mockClear();
    screen.getByRole("button", { name: /90 dias/i }).click();

    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    const [path] = mockedFetch.mock.calls[0]!;
    expect(path).toContain("end-customer-billing/revenue?from=");
  });

  it("mostra mensagem de vazio sem dados no período", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ by_month: [], by_customer: [] }),
    });

    render(<RevenueReport />);

    await waitFor(() =>
      expect(screen.getByText(/nenhum cliente comprou no período/i)).toBeInTheDocument(),
    );
  });
});
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd apps/web && pnpm vitest run __tests__/RevenueReport.test.tsx`
Expected: falha — módulo `@/components/RevenueReport` não existe.

- [ ] **Step 3: Implementar `RevenueReport`**

Criar `apps/web/src/components/RevenueReport.tsx`, reaproveitando exatamente o padrão de preset/range de `ConversationsUsageReport.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import { formatBRL, formatPhone } from "@/lib/format";

import { MonthlyBarChart } from "./MonthlyBarChart";

type ByMonth = { month: string; total_brl: number };
type ByCustomer = { contact_phone_number: string; total_brl: number };
type Report = { by_month: ByMonth[]; by_customer: ByCustomer[] };

type Preset = "7" | "30" | "90" | "custom";

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function rangeForPreset(preset: Preset): { from: string; to: string } {
  const to = new Date();
  const from = new Date();
  const days = preset === "custom" ? 30 : Number(preset);
  from.setDate(from.getDate() - days);
  return { from: isoDate(from), to: isoDate(to) };
}

export function RevenueReport() {
  const [preset, setPreset] = useState<Preset>("30");
  const [range, setRange] = useState(() => rangeForPreset("30"));
  const [report, setReport] = useState<Report>({ by_month: [], by_customer: [] });
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    async function load() {
      setLoaded(false);
      try {
        const response = await backendFetch(
          `end-customer-billing/revenue?from=${range.from}&to=${range.to}`,
        );
        if (response.ok) {
          setReport(await response.json());
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, [range]);

  function selectPreset(next: Preset) {
    setPreset(next);
    if (next !== "custom") {
      setRange(rangeForPreset(next));
    }
  }

  return (
    <div className="flex flex-1 flex-col overflow-y-auto px-8 py-6">
      <div className="flex flex-wrap items-center gap-3">
        {(["7", "30", "90"] as Preset[]).map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => selectPreset(p)}
            aria-pressed={preset === p}
            className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
              preset === p ? "bg-ink text-ground" : "text-muted hover:text-ink"
            }`}
          >
            {p} dias
          </button>
        ))}
        <button
          type="button"
          onClick={() => setPreset("custom")}
          aria-pressed={preset === "custom"}
          className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
            preset === "custom" ? "bg-ink text-ground" : "text-muted hover:text-ink"
          }`}
        >
          Personalizado
        </button>
        {preset === "custom" && (
          <div className="flex items-center gap-2 text-sm text-ink">
            <input
              type="date"
              value={range.from}
              onChange={(event) => setRange((prev) => ({ ...prev, from: event.target.value }))}
              className="rounded border border-line bg-surface px-2 py-1 text-sm"
            />
            <span className="text-muted">até</span>
            <input
              type="date"
              value={range.to}
              onChange={(event) => setRange((prev) => ({ ...prev, to: event.target.value }))}
              className="rounded border border-line bg-surface px-2 py-1 text-sm"
            />
          </div>
        )}
      </div>

      {!loaded ? (
        <p className="mt-6 text-sm text-muted">Carregando...</p>
      ) : (
        <>
          <div className="mt-6 max-w-2xl">
            <MonthlyBarChart data={report.by_month} />
          </div>

          <h3 className="mt-8 font-display text-sm font-semibold text-ink">Por cliente</h3>
          <table className="mt-3 w-full max-w-xl text-left text-sm">
            <thead>
              <tr className="border-b border-line text-xs uppercase tracking-[0.1em] text-muted">
                <th className="py-2">Contato</th>
                <th className="py-2">Faturado no período</th>
              </tr>
            </thead>
            <tbody>
              {report.by_customer.length === 0 ? (
                <tr>
                  <td className="py-4 text-sm text-muted" colSpan={2}>
                    Nenhum cliente comprou no período selecionado.
                  </td>
                </tr>
              ) : (
                report.by_customer.map((row) => (
                  <tr key={row.contact_phone_number} className="border-b border-line">
                    <td className="py-3">{formatPhone(row.contact_phone_number)}</td>
                    <td className="py-3 font-mono">R$ {formatBRL(row.total_brl)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `cd apps/web && pnpm vitest run __tests__/RevenueReport.test.tsx`
Expected: 3 passed.

- [ ] **Step 5: Escrever o teste que falha (aba nova em `EndCustomerBillingTabs`)**

Adicionar a `apps/web/__tests__/EndCustomerBillingTabs.test.tsx` (ler o arquivo por completo primeiro pra seguir o padrão de mock já usado nos testes de troca de aba existentes):

```tsx
it("mostra a aba Faturamento quando a cobrança está habilitada", async () => {
  mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ enabled: true }) });

  render(<EndCustomerBillingTabs />);

  await waitFor(() =>
    expect(screen.getByRole("button", { name: /faturamento/i })).toBeInTheDocument(),
  );
});

it("não mostra a aba Faturamento quando a cobrança está desabilitada", async () => {
  mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ enabled: false }) });

  render(<EndCustomerBillingTabs />);

  await waitFor(() =>
    expect(screen.queryByRole("button", { name: /faturamento/i })).not.toBeInTheDocument(),
  );
});
```

- [ ] **Step 6: Rodar o teste e confirmar que falha**

Run: `cd apps/web && pnpm vitest run __tests__/EndCustomerBillingTabs.test.tsx -t Faturamento`
Expected: falha — o botão "Faturamento" não existe ainda.

- [ ] **Step 7: Implementar a aba nova**

Em `apps/web/src/components/EndCustomerBillingTabs.tsx`, adicionar `"faturamento"` ao tipo `Tab` (linha 11 atual: `type Tab = "config" | "clientes" | "faturamento" | "consumo";`), importar `RevenueReport` (`import { RevenueReport } from "./RevenueReport";`), adicionar o botão da aba (mesmo padrão condicional de `enabled` já usado pra "Clientes", posicionado entre "Clientes" e "Consumo"):

```tsx
        {enabled && (
          <button
            type="button"
            onClick={() => setTab("faturamento")}
            aria-pressed={tab === "faturamento"}
            className={tabClass(tab === "faturamento")}
          >
            Faturamento
          </button>
        )}
```

E adicionar a renderização condicional junto das demais:

```tsx
      {tab === "faturamento" && enabled && <RevenueReport />}
```

- [ ] **Step 8: Rodar os testes e confirmar que passam**

Run: `cd apps/web && pnpm vitest run __tests__/EndCustomerBillingTabs.test.tsx __tests__/RevenueReport.test.tsx`
Expected: todos passam.

- [ ] **Step 9: Rodar a suíte completa e lint**

Run: `cd apps/web && pnpm test && pnpm lint`
Expected: sem falhas novas.

- [ ] **Step 10: Commit**

```bash
cd apps/web
git add src/components/RevenueReport.tsx src/components/EndCustomerBillingTabs.tsx __tests__/RevenueReport.test.tsx __tests__/EndCustomerBillingTabs.test.tsx
git commit -m "feat(web): aba Faturamento em /configuracoes/cobranca-clientes"
```

---

## Task 7: Frontend — gráfico de gasto em `/creditos`

**Files:**
- Create: `apps/web/src/components/SpendingChart.tsx`
- Modify: `apps/web/src/app/creditos/page.tsx`
- Test: `apps/web/__tests__/SpendingChart.test.tsx`

**Interfaces:**
- Consumes: `GET billing/spending?from=&to=` (Task 4), `MonthlyBarChart` (Task 5).
- Produces: `SpendingChart` — componente com filtro de período (mesmo padrão de preset) + gráfico.

- [ ] **Step 1: Escrever o teste que falha**

Criar `apps/web/__tests__/SpendingChart.test.tsx`, seguindo exatamente o mesmo padrão de mock de `RevenueReport.test.tsx` (Task 6):

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SpendingChart } from "@/components/SpendingChart";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("SpendingChart", () => {
  it("carrega e renderiza o gráfico com os dados do período", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ by_month: [{ month: "2026-07", total_brl: 350 }] }),
    });

    render(<SpendingChart />);

    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    const [path] = mockedFetch.mock.calls[0]!;
    expect(path).toContain("billing/spending?from=");
  });

  it("muda o período ao clicar num preset", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ by_month: [] }) });

    render(<SpendingChart />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());

    mockedFetch.mockClear();
    screen.getByRole("button", { name: /90 dias/i }).click();

    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
  });
});
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd apps/web && pnpm vitest run __tests__/SpendingChart.test.tsx`
Expected: falha — módulo `@/components/SpendingChart` não existe.

- [ ] **Step 3: Implementar `SpendingChart`**

Criar `apps/web/src/components/SpendingChart.tsx` — mesma estrutura de filtro de período que `RevenueReport.tsx` (Task 6), sem a tabela por cliente (não existe "cliente" nesse lado, é o próprio tenant gastando):

```tsx
"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";

import { MonthlyBarChart } from "./MonthlyBarChart";

type ByMonth = { month: string; total_brl: number };

type Preset = "7" | "30" | "90" | "custom";

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function rangeForPreset(preset: Preset): { from: string; to: string } {
  const to = new Date();
  const from = new Date();
  const days = preset === "custom" ? 30 : Number(preset);
  from.setDate(from.getDate() - days);
  return { from: isoDate(from), to: isoDate(to) };
}

export function SpendingChart() {
  const [preset, setPreset] = useState<Preset>("30");
  const [range, setRange] = useState(() => rangeForPreset("30"));
  const [byMonth, setByMonth] = useState<ByMonth[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    async function load() {
      setLoaded(false);
      try {
        const response = await backendFetch(`billing/spending?from=${range.from}&to=${range.to}`);
        if (response.ok) {
          setByMonth((await response.json()).by_month);
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, [range]);

  function selectPreset(next: Preset) {
    setPreset(next);
    if (next !== "custom") {
      setRange(rangeForPreset(next));
    }
  }

  return (
    <div>
      <h2 className="font-display text-lg font-semibold text-ink">Gasto com créditos</h2>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {(["7", "30", "90"] as Preset[]).map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => selectPreset(p)}
            aria-pressed={preset === p}
            className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
              preset === p ? "bg-ink text-ground" : "text-muted hover:text-ink"
            }`}
          >
            {p} dias
          </button>
        ))}
        <button
          type="button"
          onClick={() => setPreset("custom")}
          aria-pressed={preset === "custom"}
          className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
            preset === "custom" ? "bg-ink text-ground" : "text-muted hover:text-ink"
          }`}
        >
          Personalizado
        </button>
        {preset === "custom" && (
          <div className="flex items-center gap-2 text-sm text-ink">
            <input
              type="date"
              value={range.from}
              onChange={(event) => setRange((prev) => ({ ...prev, from: event.target.value }))}
              className="rounded border border-line bg-surface px-2 py-1 text-sm"
            />
            <span className="text-muted">até</span>
            <input
              type="date"
              value={range.to}
              onChange={(event) => setRange((prev) => ({ ...prev, to: event.target.value }))}
              className="rounded border border-line bg-surface px-2 py-1 text-sm"
            />
          </div>
        )}
      </div>
      {!loaded ? (
        <p className="mt-4 text-sm text-muted">Carregando...</p>
      ) : (
        <div className="mt-4 max-w-2xl">
          <MonthlyBarChart data={byMonth} />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `cd apps/web && pnpm vitest run __tests__/SpendingChart.test.tsx`
Expected: 2 passed.

- [ ] **Step 5: Integrar em `/creditos`**

Em `apps/web/src/app/creditos/page.tsx`, importar `SpendingChart` e adicionar antes de `CreditosExtrato` (dentro do mesmo `<div className="px-8 pb-8">`):

```tsx
import { CreditosExtrato } from "@/components/CreditosExtrato";
import { CreditosPanel } from "@/components/CreditosPanel";
import { SpendingChart } from "@/components/SpendingChart";
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

export default async function CreditosPage() {
  const packages = await getPackages();

  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="creditos" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <CreditosPanel packages={packages} />
        <div className="px-8 pb-8">
          <SpendingChart />
          <div className="mt-8">
            <CreditosExtrato />
          </div>
        </div>
      </main>
    </div>
  );
}
```

- [ ] **Step 6: Rodar a suíte completa e lint**

Run: `cd apps/web && pnpm test && pnpm lint`
Expected: sem falhas novas.

- [ ] **Step 7: Commit**

```bash
cd apps/web
git add src/components/SpendingChart.tsx src/app/creditos/page.tsx __tests__/SpendingChart.test.tsx
git commit -m "feat(web): gráfico de gasto mensal com créditos em /creditos"
```

---

## Task 8: Documentação (`CLAUDE.md`)

**Files:**
- Modify: `/home/falcao/development/advoxs/CLAUDE.md`

**Interfaces:**
- Consumes: nada de código — só reflete o que as Tasks 1-7 implementaram.

- [ ] **Step 1: Atualizar a seção "Cobrança do cliente final" e a seção Frontend/`/creditos`**

Na seção "Cobrança do cliente final", depois do parágrafo que já documenta `ConnectEarnings`/saldo disponível, adicionar um parágrafo novo descrevendo: a tabela `end_customer_subscription_payments` (histórico de pagamento de assinatura, alimentada pelo webhook de renovação), o endpoint `GET /end-customer-billing/revenue` (faturamento por mês/cliente, só pra `billing_provider="connect"`, 100% do nosso banco — nunca uma chamada à Stripe em tempo de leitura), e a aba "Faturamento" no painel. Na seção Frontend/`/creditos`, adicionar a menção ao `GET /billing/spending` e ao gráfico de gasto mensal. Referenciar a spec: `docs/superpowers/specs/2026-07-28-dashboard-financeiro-tenant-design.md`. Escrever o texto real (sem placeholder) refletindo o que foi de fato implementado — verificar contra o código final das Tasks 1-7, não contra este plano, exatamente como já foi feito nas features anteriores desta sessão.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: documenta o dashboard financeiro por tenant (faturamento + gasto)"
```
