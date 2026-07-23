# Gate único determinístico — remoção do mecanismo antigo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aposentar o mecanismo antigo de billing (embutido no grafo do `apps/agents`) — o gate determinístico (`apps/worker/app/billing_gate.py`) passa a ser o único comportamento sempre que `tenant_billing_settings.enabled = true`, sem exceção e sem configuração por tenant.

**Architecture:** Remoção de código em 3 serviços. `apps/api`: dropa a coluna `insufficient_balance_policy` (migration) e simplifica `_send_purchase_confirmation` pra um único caminho. `apps/worker`: `maybe_enter_gate` perde a condição de policy; `process_inbound_message` para de montar/enviar `end_customer_billing` pro `agents` (o gate já intercepta tudo antes). `apps/agents`: remove a tool `gerar_link_pagamento_cliente`, `is_billing_blocked`, o bounce/prompt/aviso de retorno em `agent_node`, e o campo `end_customer_billing` do contrato inteiro (schema → `run_agent` → `State`).

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (`apps/api`), Arq + SQLAlchemy Core (`apps/worker`), FastAPI + LangGraph (`apps/agents`).

## Global Constraints

- **Sem UI nova** em `apps/web` — nada muda lá.
- **Migra todo tenant automaticamente** — a migration que dropa a coluna já resolve isso: sem a coluna, não existe mais distinção nenhuma, todo tenant com `enabled=true` já é determinístico.
- **Remove código morto de verdade, não desliga** — cada peça do mecanismo antigo é apagada, não só deixada sem uso.
- **`billing_gate_welcome_text` continua existindo** (customização de texto, ortogonal ao mecanismo) — não é tocado.
- **Nenhuma mudança em `customer_funded`** (quem paga o turno) — isso já é decidido só por `enabled`/`exempt`/`balance`, nunca dependeu de policy.
- **Testes de `apps/agents`**: rodar sempre com `python3 -m pytest`/`python3 -m ruff` (nunca `uv run` — venv quebrado nesse app específico).

---

### Task 1: Migration — remove `insufficient_balance_policy`

**Files:**
- Create: `apps/api/alembic/versions/0020_remove_insufficient_balance_policy.py`

**Interfaces:**
- Consumes: nada.
- Produces: ausência da coluna `tenant_billing_settings.insufficient_balance_policy` — consumida (pela ausência) pela Task 2.

- [ ] **Step 1: Criar a migration**

Crie `apps/api/alembic/versions/0020_remove_insufficient_balance_policy.py`:

```python
"""remove insufficient_balance_policy — gate único determinístico

O billing gate determinístico deixou de ser opcional: não existe mais
rollout gradual por tenant, é o único mecanismo quando
tenant_billing_settings.enabled = true. A coluna que decidia entre os dois
mecanismos (`block_with_message` | `deterministic_gate`) não tem mais
nenhum valor possível além de um só comportamento, então é removida — ver
docs/superpowers/specs/2026-07-23-gate-unico-deterministico-design.md.

`billing_gate_welcome_text` (mesma tabela) permanece — é customização de
texto do tenant, não decide mecanismo.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-23
"""

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("tenant_billing_settings", "insufficient_balance_policy")


def downgrade() -> None:
    op.add_column(
        "tenant_billing_settings",
        sa.Column(
            "insufficient_balance_policy",
            sa.String(),
            server_default=sa.text("'block_with_message'"),
            nullable=False,
        ),
    )
```

- [ ] **Step 2: Verificar a migration**

Se houver Postgres real disponível (`docker compose ps postgres`): `cd apps/api && uv run alembic upgrade head`, confirme via `psql` (`\d tenant_billing_settings`) que a coluna não existe mais, depois `uv run alembic downgrade -1 && uv run alembic upgrade head` pra confirmar que sobe/desce/sobe limpo. Sem Postgres disponível, valide a sintaxe: `python3 -c "import ast; ast.parse(open('apps/api/alembic/versions/0020_remove_insufficient_balance_policy.py').read())"`.

- [ ] **Step 3: Commit**

```bash
git add apps/api/alembic/versions/0020_remove_insufficient_balance_policy.py
git commit -m "feat(api): migration removendo insufficient_balance_policy — gate único determinístico"
```

---

### Task 2: `apps/api` — model + serviço + webhook simplificados

**Files:**
- Modify: `apps/api/app/models/end_customer_billing.py`
- Modify: `apps/api/app/services/end_customer_billing.py`
- Modify: `apps/api/app/api/v1/webhooks/stripe_tenant.py`
- Modify: `apps/api/tests/unit/test_end_customer_billing_service.py`
- Modify: `apps/api/tests/unit/test_stripe_tenant_webhook.py`

**Interfaces:**
- Consumes: migration `0020` (Task 1).
- Produces: `_send_purchase_confirmation(session, tenant_id, contact_phone_number)` (sem `arq`) — não é consumido por nenhuma outra task deste plano.

- [ ] **Step 1: Remover o campo do model**

Em `apps/api/app/models/end_customer_billing.py`, troque:

```python
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    billing_mode: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'credits'")
    )
    # Único valor suportado por ora — hook de extensibilidade (como billing_mode).
    insufficient_balance_policy: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'block_with_message'")
    )
    billing_gate_welcome_text: Mapped[str | None] = mapped_column(Text)
```

por:

```python
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    billing_mode: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'credits'")
    )
    billing_gate_welcome_text: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 2: Atualizar os testes do serviço (TDD — escrever as versões novas primeiro)**

Em `apps/api/tests/unit/test_end_customer_billing_service.py`, troque o helper `_conversation`:

```python
def _conversation(**overrides):
    row = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        contact_phone_number=CONTACT,
        last_message_at=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row
```

por:

```python
def _conversation(**overrides):
    row = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        contact_phone_number=CONTACT,
        last_message_at=None,
        state="agent",
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row
```

Troque toda a classe `TestProcessEndCustomerCheckoutCompleted` (os 5 testes que usam `session.scalar.side_effect` com o item de policy, mais os 2 testes específicos de branch) — de:

```python
    async def test_credita_saldo_novo_e_manda_confirmacao(self, session, arq, monkeypatch) -> None:
        package = _package()
        conversation = _conversation()
        number = _number()
        session.scalar = AsyncMock(
            side_effect=[None, package, None, "block_with_message", conversation, number]
        )
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        session.flush = AsyncMock()
        send = AsyncMock()
        monkeypatch.setattr(service, "send_text_message", send)
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        balance, transaction, message, trigger_message = added
        assert balance.credit_balance == package.credits_granted
        assert transaction.type == "purchase"
        assert transaction.amount_credits == package.credits_granted
        assert transaction.stripe_payment_id == "cs_end_999"
        assert message.sender_type == "system"
        assert trigger_message.sender_type == "system"
        assert "pagamento" in trigger_message.content.lower()
        send.assert_awaited_once()
        assert send.await_args.kwargs["to"] == CONTACT
        session.commit.assert_awaited()

        # Aciona o agente pela mesma fila do webhook do WhatsApp, com o id da
        # mensagem de gatilho — é isso que faz a Sofia reagir sozinha, sem
        # depender do cliente digitar "já paguei".
        arq.enqueue_job.assert_awaited_once_with(
            "process_inbound_message",
            tenant_id=str(TENANT_ID),
            conversation_id=str(conversation.id),
            message_id=str(trigger_message.id),
        )

    async def test_credita_saldo_existente_soma(self, session, arq, monkeypatch) -> None:
        package = _package()
        existing_balance = SimpleNamespace(
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            credit_balance=100,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.scalar = AsyncMock(
            side_effect=[None, package, existing_balance, "block_with_message", None, None]
        )
        session.add = MagicMock()
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        assert existing_balance.credit_balance == 100 + package.credits_granted

    async def test_falha_ao_confirmar_via_whatsapp_nao_impede_credito(
        self, session, arq, monkeypatch
    ) -> None:
        package = _package()
        session.scalar = AsyncMock(
            side_effect=[None, package, None, "block_with_message", None, None]
        )
        session.add = MagicMock()
        monkeypatch.setattr(
            service, "send_text_message", AsyncMock(side_effect=RuntimeError("falhou"))
        )

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        # Falha antes de chegar na mensagem de gatilho — não deve acionar o agente.
        arq.enqueue_job.assert_not_called()

        session.commit.assert_awaited()

    async def test_transiciona_billing_gate_para_agent_quando_deterministic_gate(
        self, session, arq, monkeypatch
    ) -> None:
        package = _package()
        conversation = _conversation(
            state="billing_gate", billing_gate_step="aguardando_pagamento", billing_gate_retries=1
        )
        number = _number()
        session.scalar = AsyncMock(
            side_effect=[None, package, None, "deterministic_gate", conversation, number]
        )
        session.add = MagicMock()
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        assert conversation.state == "agent"
        assert conversation.billing_gate_step is None
        assert conversation.billing_gate_retries == 0
        arq.enqueue_job.assert_not_called()

    async def test_nao_transiciona_conversa_em_human_mesmo_com_deterministic_gate(
        self, session, arq, monkeypatch
    ) -> None:
        package = _package()
        conversation = _conversation(state="human")
        number = _number()
        session.scalar = AsyncMock(
            side_effect=[None, package, None, "deterministic_gate", conversation, number]
        )
        session.add = MagicMock()
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        assert conversation.state == "human"
```

por:

```python
    async def test_credita_saldo_novo_e_manda_confirmacao(self, session, arq, monkeypatch) -> None:
        package = _package()
        conversation = _conversation()
        number = _number()
        session.scalar = AsyncMock(side_effect=[None, package, None, conversation, number])
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        session.flush = AsyncMock()
        send = AsyncMock()
        monkeypatch.setattr(service, "send_text_message", send)
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        balance, transaction, message = added
        assert balance.credit_balance == package.credits_granted
        assert transaction.type == "purchase"
        assert transaction.amount_credits == package.credits_granted
        assert transaction.stripe_payment_id == "cs_end_999"
        assert message.sender_type == "system"
        send.assert_awaited_once()
        assert send.await_args.kwargs["to"] == CONTACT
        session.commit.assert_awaited()
        # Mecanismo antigo (mensagem de gatilho pro agents) foi removido —
        # nunca mais aciona nada por fila.
        arq.enqueue_job.assert_not_called()

    async def test_credita_saldo_existente_soma(self, session, arq, monkeypatch) -> None:
        package = _package()
        existing_balance = SimpleNamespace(
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            credit_balance=100,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.scalar = AsyncMock(side_effect=[None, package, existing_balance, None, None])
        session.add = MagicMock()
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        assert existing_balance.credit_balance == 100 + package.credits_granted

    async def test_falha_ao_confirmar_via_whatsapp_nao_impede_credito(
        self, session, arq, monkeypatch
    ) -> None:
        package = _package()
        session.scalar = AsyncMock(side_effect=[None, package, None, None, None])
        session.add = MagicMock()
        monkeypatch.setattr(
            service, "send_text_message", AsyncMock(side_effect=RuntimeError("falhou"))
        )

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        arq.enqueue_job.assert_not_called()
        session.commit.assert_awaited()

    async def test_transiciona_billing_gate_para_agent(self, session, arq, monkeypatch) -> None:
        package = _package()
        conversation = _conversation(
            state="billing_gate", billing_gate_step="aguardando_pagamento", billing_gate_retries=1
        )
        number = _number()
        session.scalar = AsyncMock(side_effect=[None, package, None, conversation, number])
        session.add = MagicMock()
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        assert conversation.state == "agent"
        assert conversation.billing_gate_step is None
        assert conversation.billing_gate_retries == 0
        arq.enqueue_job.assert_not_called()

    async def test_nao_transiciona_conversa_em_human(self, session, arq, monkeypatch) -> None:
        package = _package()
        conversation = _conversation(state="human")
        number = _number()
        session.scalar = AsyncMock(side_effect=[None, package, None, conversation, number])
        session.add = MagicMock()
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        assert conversation.state == "human"
```

- [ ] **Step 3: Rodar e confirmar a falha**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py::TestProcessEndCustomerCheckoutCompleted -v`
Expected: FAIL — o código de produção ainda espera 6 itens no `side_effect` (com a policy) e ainda cria `trigger_message`/chama `enqueue_job`; os testes novos (5 itens, sem trigger_message, `enqueue_job.assert_not_called()`) não batem com o comportamento atual.

- [ ] **Step 4: Simplificar o serviço**

Em `apps/api/app/services/end_customer_billing.py`, troque o import:

```python
from arq.connections import ArqRedis
```

por (remove — `ArqRedis` deixa de ser usado no arquivo depois desta task):

(nada — só apague a linha)

Troque:

```python
async def process_end_customer_checkout_completed(
    session: AsyncSession, tenant_id: uuid.UUID, stripe_session: dict, arq: ArqRedis
) -> None:
```

por:

```python
async def process_end_customer_checkout_completed(
    session: AsyncSession, tenant_id: uuid.UUID, stripe_session: dict
) -> None:
```

Troque:

```python
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

    policy = await session.scalar(
        select(TenantBillingSettings.insufficient_balance_policy).where(
            TenantBillingSettings.tenant_id == tenant_id
        )
    )
    await _send_purchase_confirmation(
        session, tenant_id, contact_phone_number, arq, policy or "block_with_message"
    )
```

por:

```python
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
```

Troque a função `_send_purchase_confirmation` inteira:

```python
async def _send_purchase_confirmation(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    contact_phone_number: str,
    arq: ArqRedis,
    insufficient_balance_policy: str,
) -> None:
    """Best-effort: uma falha ao mandar a confirmação não desfaz o crédito
    já commitado acima — o cliente só não recebe o aviso, mas o saldo está lá.

    Além do aviso instantâneo (fixo, via WhatsApp direto), o comportamento
    depois disso depende de insufficient_balance_policy (rollout gradual do
    billing gate determinístico, ver
    docs/superpowers/specs/2026-07-22-billing-gate-deterministico-design.md):
    - "block_with_message" (default, tenant ainda não migrado): aciona o
      próprio agente com uma mensagem de sistema avisando que o pagamento
      foi concluído — mesma fila (process_inbound_message) que o webhook do
      WhatsApp usa. Isso faz a Sofia reagir e efetivar a transferência
      sozinha, sem depender do cliente digitar "já paguei".
    - "deterministic_gate" (tenant migrado): a conversa, se estiver em
      billing_gate, volta direto pra "agent" — sem acionar o agents, já que
      o checkpoint do LangGraph nunca foi tocado por essa mudança de estado
      e a conversa retoma de onde estava (ou começa do zero pelo ponto de
      entrada, se nunca tinha sido atendida).
    """
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

        if insufficient_balance_policy == "deterministic_gate":
            if conversation.state == "billing_gate":
                conversation.state = "agent"
                conversation.billing_gate_step = None
                conversation.billing_gate_retries = 0
            await session.commit()
            return

        trigger_message = Message(
            conversation_id=conversation.id,
            tenant_id=tenant_id,
            sender_type="system",
            content=(
                "O cliente concluiu o pagamento do pacote de créditos com sucesso"
                " — saldo já disponível."
            ),
        )
        session.add(trigger_message)
        await session.commit()
        await session.refresh(trigger_message)

        await arq.enqueue_job(
            "process_inbound_message",
            tenant_id=str(tenant_id),
            conversation_id=str(conversation.id),
            message_id=str(trigger_message.id),
        )
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

por:

```python
async def _send_purchase_confirmation(
    session: AsyncSession, tenant_id: uuid.UUID, contact_phone_number: str
) -> None:
    """Best-effort: uma falha ao mandar a confirmação não desfaz o crédito
    já commitado acima — o cliente só não recebe o aviso, mas o saldo está lá.

    Além do aviso instantâneo (fixo, via WhatsApp direto), a conversa (se
    estiver em billing_gate) volta direto pra "agent" — sem acionar o
    agents, já que o checkpoint do LangGraph nunca foi tocado por essa
    mudança de estado e a conversa retoma de onde estava (ou começa do zero
    pelo ponto de entrada, se nunca tinha sido atendida).
    """
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

        if conversation.state == "billing_gate":
            conversation.state = "agent"
            conversation.billing_gate_step = None
            conversation.billing_gate_retries = 0
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

- [ ] **Step 5: Rodar e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_end_customer_billing_service.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 6: Atualizar o webhook e seu teste (o parâmetro `arq` fica órfão)**

Em `apps/api/app/api/v1/webhooks/stripe_tenant.py`, troque:

```python
import stripe
from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_tenant_secret
from app.core.db import get_system_session
from app.core.queue import get_arq_pool
from app.models import TenantBillingSettings
from app.services.end_customer_billing import process_end_customer_checkout_completed
```

por:

```python
import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_tenant_secret
from app.core.db import get_system_session
from app.models import TenantBillingSettings
from app.services.end_customer_billing import process_end_customer_checkout_completed
```

Troque:

```python
async def receive_tenant_webhook(
    tenant_id: uuid.UUID,
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    session: AsyncSession = Depends(get_system_session),
    arq: ArqRedis = Depends(get_arq_pool),
) -> dict:
```

por:

```python
async def receive_tenant_webhook(
    tenant_id: uuid.UUID,
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    session: AsyncSession = Depends(get_system_session),
) -> dict:
```

Troque:

```python
    if event["type"] == "checkout.session.completed":
        await process_end_customer_checkout_completed(
            session, tenant_id, event["data"]["object"], arq
        )
```

por:

```python
    if event["type"] == "checkout.session.completed":
        await process_end_customer_checkout_completed(session, tenant_id, event["data"]["object"])
```

Em `apps/api/tests/unit/test_stripe_tenant_webhook.py`, troque:

```python
import app.api.v1.webhooks.stripe_tenant as webhook_module
from app.core.db import get_system_session
from app.core.queue import get_arq_pool
from app.main import app
```

por:

```python
import app.api.v1.webhooks.stripe_tenant as webhook_module
from app.core.db import get_system_session
from app.main import app
```

Troque:

```python
@pytest.fixture
def client(session):
    async def override_session():
        yield session

    async def override_arq():
        return AsyncMock()

    app.dependency_overrides[get_system_session] = override_session
    app.dependency_overrides[get_arq_pool] = override_arq
    yield TestClient(app)
    app.dependency_overrides.clear()
```

por:

```python
@pytest.fixture
def client(session):
    async def override_session():
        yield session

    app.dependency_overrides[get_system_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()
```

- [ ] **Step 7: Rodar a suíte completa + lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Expected: tudo passa, lint limpo (confirme que `AsyncMock` continua usado em outro teste do arquivo do webhook — se sobrar um import não usado, remova).

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/models/end_customer_billing.py apps/api/app/services/end_customer_billing.py apps/api/app/api/v1/webhooks/stripe_tenant.py apps/api/tests/unit/test_end_customer_billing_service.py apps/api/tests/unit/test_stripe_tenant_webhook.py
git commit -m "feat(api): remove insufficient_balance_policy — _send_purchase_confirmation sempre determinística"
```

---

### Task 3: `apps/worker` — `_load_context`/`InboundContext`/`maybe_enter_gate` sem policy

**Files:**
- Modify: `apps/worker/app/tasks/inbound_context.py`
- Modify: `apps/worker/app/tasks/messages.py`
- Modify: `apps/worker/app/billing_gate.py`
- Modify: `apps/worker/tests/unit/test_load_context.py`
- Modify: `apps/worker/tests/unit/test_billing_gate.py`

**Interfaces:**
- Consumes: migration `0020` (Task 1, mesmo banco — mas o `tables.py` do worker já não tem essa coluna mapeada, então nada muda ali).
- Produces: `InboundContext` sem `insufficient_balance_policy`, `maybe_enter_gate` sem condição de policy — consumido pela Task 4.

- [ ] **Step 1: Atualizar os testes de `_load_context` (TDD)**

Em `apps/worker/tests/unit/test_load_context.py`, troque:

```python
async def test_billing_habilitado_le_saldo_e_pacotes() -> None:
    billing_settings = SimpleNamespace(
        enabled=True, insufficient_balance_policy="block_with_message", billing_gate_welcome_text=None
    )
```

por:

```python
async def test_billing_habilitado_le_saldo_e_pacotes() -> None:
    billing_settings = SimpleNamespace(enabled=True, billing_gate_welcome_text=None)
```

Troque:

```python
async def test_billing_habilitado_sem_saldo_ainda_usa_zero() -> None:
    billing_settings = SimpleNamespace(
        enabled=True, insufficient_balance_policy="block_with_message", billing_gate_welcome_text=None
    )
```

por:

```python
async def test_billing_habilitado_sem_saldo_ainda_usa_zero() -> None:
    billing_settings = SimpleNamespace(enabled=True, billing_gate_welcome_text=None)
```

Troque os 2 testes que cobrem exclusivamente o campo removido:

```python
async def test_carrega_policy_e_texto_de_boas_vindas_do_tenant() -> None:
    billing_settings = SimpleNamespace(
        enabled=True,
        insufficient_balance_policy="deterministic_gate",
        billing_gate_welcome_text="Bem-vindo ao nosso escritório!",
    )
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=billing_settings,
        balance=0,
        packages=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.insufficient_balance_policy == "deterministic_gate"
    assert context.billing_gate_welcome_text == "Bem-vindo ao nosso escritório!"


async def test_sem_billing_settings_usa_policy_default() -> None:
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

    assert context.insufficient_balance_policy == "block_with_message"
    assert context.billing_gate_welcome_text is None
```

por:

```python
async def test_carrega_texto_de_boas_vindas_do_tenant() -> None:
    billing_settings = SimpleNamespace(
        enabled=True, billing_gate_welcome_text="Bem-vindo ao nosso escritório!"
    )
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=billing_settings,
        balance=0,
        packages=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.billing_gate_welcome_text == "Bem-vindo ao nosso escritório!"


async def test_sem_billing_settings_usa_welcome_text_none() -> None:
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

    assert context.billing_gate_welcome_text is None
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd apps/worker && uv run pytest tests/unit/test_load_context.py -v`
Expected: FAIL — `InboundContext` ainda tem `insufficient_balance_policy`, mas os testes novos não checam mais esse campo (os 2 testes renomeados vão passar aleatoriamente por não testarem o campo removido, então confirme a falha olhando os 2 primeiros: eles vão dar `TypeError` ou `AttributeError` se a query ainda tentar ler `insufficient_balance_policy` do `SimpleNamespace` que não tem mais esse atributo).

- [ ] **Step 3: Remover o campo de `InboundContext`**

Em `apps/worker/app/tasks/inbound_context.py`, troque:

```python
    billing_gate_step: str | None = None
    billing_gate_retries: int = 0
    billing_gate_checkout_url: str | None = None
    insufficient_balance_policy: str = "block_with_message"
    billing_gate_welcome_text: str | None = None
    end_customer_billing_exempt: bool = False
```

por:

```python
    billing_gate_step: str | None = None
    billing_gate_retries: int = 0
    billing_gate_checkout_url: str | None = None
    billing_gate_welcome_text: str | None = None
    end_customer_billing_exempt: bool = False
```

- [ ] **Step 4: Atualizar `_load_context`**

Em `apps/worker/app/tasks/messages.py`, troque:

```python
    billing_settings = (
        await session.execute(
            select(
                tables.tenant_billing_settings.c.enabled,
                tables.tenant_billing_settings.c.insufficient_balance_policy,
                tables.tenant_billing_settings.c.billing_gate_welcome_text,
            ).where(tables.tenant_billing_settings.c.tenant_id == uuid.UUID(tenant_id))
        )
    ).one_or_none()
```

por:

```python
    billing_settings = (
        await session.execute(
            select(
                tables.tenant_billing_settings.c.enabled,
                tables.tenant_billing_settings.c.billing_gate_welcome_text,
            ).where(tables.tenant_billing_settings.c.tenant_id == uuid.UUID(tenant_id))
        )
    ).one_or_none()
```

Troque:

```python
        billing_gate_checkout_url=conversation.billing_gate_checkout_url,
        insufficient_balance_policy=(
            billing_settings.insufficient_balance_policy
            if billing_settings is not None
            else "block_with_message"
        ),
        billing_gate_welcome_text=(
            billing_settings.billing_gate_welcome_text if billing_settings is not None else None
        ),
        end_customer_billing_exempt=conversation.end_customer_billing_exempt,
    )
```

por:

```python
        billing_gate_checkout_url=conversation.billing_gate_checkout_url,
        billing_gate_welcome_text=(
            billing_settings.billing_gate_welcome_text if billing_settings is not None else None
        ),
        end_customer_billing_exempt=conversation.end_customer_billing_exempt,
    )
```

- [ ] **Step 5: Rodar e confirmar sucesso**

Run: `cd apps/worker && uv run pytest tests/unit/test_load_context.py -v`
Expected: todos os testes do arquivo passam. `test_sem_agentes_retorna_lista_vazia`'s `assert session.execute.await_count == 7` continua valendo — só estreitamos a lista de colunas de uma query já existente.

- [ ] **Step 6: Atualizar `maybe_enter_gate` e seus testes**

Em `apps/worker/tests/unit/test_billing_gate.py`, troque o helper `_inbound`:

```python
def _inbound(**overrides) -> InboundContext:
    base = InboundContext(
        conversation_state="agent",
        contact_phone_number="5511999998888",
        message_content="oi",
        phone_number_id="PNID",
        access_token_encrypted="cifrado",
        credit_balance=Decimal(1000),
        end_customer_billing_enabled=True,
        end_customer_balance=Decimal(0),
        end_customer_packages=PACKAGES,
        agents=[],
        insufficient_balance_policy="deterministic_gate",
        billing_gate_step=None,
        billing_gate_retries=0,
        billing_gate_checkout_url=None,
        billing_gate_welcome_text=None,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base
```

por:

```python
def _inbound(**overrides) -> InboundContext:
    base = InboundContext(
        conversation_state="agent",
        contact_phone_number="5511999998888",
        message_content="oi",
        phone_number_id="PNID",
        access_token_encrypted="cifrado",
        credit_balance=Decimal(1000),
        end_customer_billing_enabled=True,
        end_customer_balance=Decimal(0),
        end_customer_packages=PACKAGES,
        agents=[],
        billing_gate_step=None,
        billing_gate_retries=0,
        billing_gate_checkout_url=None,
        billing_gate_welcome_text=None,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base
```

Em `class TestMaybeEnterGate`, troque:

```python
    async def test_entra_no_gate_quando_policy_deterministic_e_sem_saldo(self) -> None:
        session = AsyncMock()
        inbound = _inbound(conversation_state="agent", end_customer_balance=Decimal(0))

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is True
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()

    async def test_nao_entra_quando_policy_e_block_with_message(self) -> None:
        session = AsyncMock()
        inbound = _inbound(insufficient_balance_policy="block_with_message")

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is False
        session.execute.assert_not_called()
```

por:

```python
    async def test_entra_no_gate_quando_habilitado_e_sem_saldo(self) -> None:
        session = AsyncMock()
        inbound = _inbound(conversation_state="agent", end_customer_balance=Decimal(0))

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is True
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()
```

(os outros testes da classe — `test_nao_entra_com_saldo_positivo`, `test_ja_em_billing_gate_retorna_true_sem_reprocessar_entrada`, `test_nao_entra_quando_contato_esta_isento`, `test_gate_ativo_mas_ja_isento_sai_do_gate_e_libera_o_turno` — continuam sem alteração, nenhum deles referencia policy.)

- [ ] **Step 7: Rodar e confirmar a falha**

Run: `cd apps/worker && uv run pytest tests/unit/test_billing_gate.py::TestMaybeEnterGate -v`
Expected: FAIL — o `_inbound()` novo não passa mais `insufficient_balance_policy`, mas o dataclass ainda tem esse campo com default `"block_with_message"`; o código de produção ainda EXIGE `policy == "deterministic_gate"` pra entrar no gate — como o default agora nunca é setado como `"deterministic_gate"` por nenhum teste, `test_entra_no_gate_quando_habilitado_e_sem_saldo` falha (`entered` viria `False`, não `True`).

- [ ] **Step 8: Remover a condição em `maybe_enter_gate`**

Em `apps/worker/app/billing_gate.py`, troque o docstring do módulo:

```python
"""Máquina de estados do billing gate determinístico — conduz o diálogo
mecânico (sem LLM) de "sem saldo -> escolher pacote -> pagar -> liberado"
pro cliente final, só pra tenants com insufficient_balance_policy =
"deterministic_gate" (rollout gradual, ver
docs/superpowers/specs/2026-07-22-billing-gate-deterministico-design.md).
Tenants ainda em "block_with_message" (o default) nunca chegam aqui — o
fluxo antigo (dentro do agents) continua valendo pra eles, sem mudança."""
```

por:

```python
"""Máquina de estados do billing gate determinístico — conduz o diálogo
mecânico (sem LLM) de "sem saldo -> escolher pacote -> pagar -> liberado"
pro cliente final, sempre que tenant_billing_settings.enabled = true — é o
único mecanismo de cobrança do cliente final que existe (ver
docs/superpowers/specs/2026-07-23-gate-unico-deterministico-design.md)."""
```

Troque:

```python
    if (
        inbound.conversation_state == "agent"
        and inbound.end_customer_billing_enabled
        and not inbound.end_customer_billing_exempt
        and inbound.insufficient_balance_policy == "deterministic_gate"
        and inbound.end_customer_balance <= 0
    ):
```

por:

```python
    if (
        inbound.conversation_state == "agent"
        and inbound.end_customer_billing_enabled
        and not inbound.end_customer_billing_exempt
        and inbound.end_customer_balance <= 0
    ):
```

- [ ] **Step 9: Rodar e confirmar sucesso**

Run: `cd apps/worker && uv run pytest tests/unit/test_billing_gate.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 10: Rodar a suíte completa do worker**

Run: `cd apps/worker && uv run pytest tests/unit -q`
Expected: tudo passa (a Task 4 ainda vai tocar `test_process_inbound_message.py` — não se preocupe se algo ali quebrar por causa da Task 4, só confirme que `test_load_context.py`/`test_billing_gate.py` estão 100% verdes).

- [ ] **Step 11: Commit**

```bash
git add apps/worker/app/tasks/inbound_context.py apps/worker/app/tasks/messages.py apps/worker/app/billing_gate.py apps/worker/tests/unit/test_load_context.py apps/worker/tests/unit/test_billing_gate.py
git commit -m "feat(worker): remove insufficient_balance_policy — gate determinístico sempre que habilitado"
```

---

### Task 4: `apps/worker` — para de enviar `end_customer_billing` pro `agents`

**Files:**
- Modify: `apps/worker/app/tasks/messages.py`
- Modify: `apps/worker/app/clients/agents.py`
- Modify: `apps/worker/tests/unit/test_process_inbound_message.py`

**Interfaces:**
- Consumes: nada de outra task.
- Produces: `send_message_to_agents(...)` sem o parâmetro `end_customer_billing` — consumido pela Task 7 (que remove o campo do lado do `agents`, o receptor).

- [ ] **Step 1: Atualizar os testes que checam o payload (TDD)**

Em `apps/worker/tests/unit/test_process_inbound_message.py`, troque:

```python
async def test_moeda_unica_debita_so_o_cliente_final(patched) -> None:
    patched["load"].return_value = _inbound_com_billing(balance=1000)
    patched["send"].return_value = {"responses": ["oi"], "tokens_used": 2000}

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()
    assert patched["send"].await_args.kwargs["end_customer_billing"]["balance"] == 1000
    # Moeda única: o turno custeado pelo cliente NÃO debita o tenant de novo.
    patched["debitar"].assert_not_awaited()
    patched["debitar_cliente_final"].assert_awaited_once()
    args = patched["debitar_cliente_final"].await_args.args
    assert args[4] == 2000  # tokens_used
    # Sem breakdown na resposta -> fallback: tudo como output -> 2000/1000 = 2
    assert args[5] == Decimal("2")
    assert args[8] == PRICING_CONFIG.id
```

por:

```python
async def test_moeda_unica_debita_so_o_cliente_final(patched) -> None:
    patched["load"].return_value = _inbound_com_billing(balance=1000)
    patched["send"].return_value = {"responses": ["oi"], "tokens_used": 2000}

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()
    # Moeda única: o turno custeado pelo cliente NÃO debita o tenant de novo.
    patched["debitar"].assert_not_awaited()
    patched["debitar_cliente_final"].assert_awaited_once()
    args = patched["debitar_cliente_final"].await_args.args
    assert args[4] == 2000  # tokens_used
    # Sem breakdown na resposta -> fallback: tudo como output -> 2000/1000 = 2
    assert args[5] == Decimal("2")
    assert args[8] == PRICING_CONFIG.id
```

Troque:

```python
async def test_billing_habilitado_sem_saldo_debita_o_tenant(patched) -> None:
    # Cliente sem saldo: a secretária oferece pacotes — turno custeado pelo tenant.
    patched["load"].return_value = _inbound_com_billing(balance=0)
    patched["send"].return_value = {"responses": ["oi"], "tokens_used": 2000}

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()
    assert patched["send"].await_args.kwargs["end_customer_billing"]["balance"] == 0
    patched["debitar_cliente_final"].assert_not_awaited()
    patched["debitar"].assert_awaited_once()
```

por:

```python
async def test_billing_habilitado_sem_saldo_debita_o_tenant(patched) -> None:
    # Cliente sem saldo: quem paga esse turno é o tenant (o billing gate
    # determinístico já teria interceptado antes se fosse esse o cenário —
    # este teste cobre o caso em que o gate não intercepta, ex: exemption).
    patched["load"].return_value = _inbound_com_billing(balance=0)
    patched["send"].return_value = {"responses": ["oi"], "tokens_used": 2000}

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()
    patched["debitar_cliente_final"].assert_not_awaited()
    patched["debitar"].assert_awaited_once()
```

Remova o teste inteiro (fica redundante — a ausência do payload já é universal, testada implicitamente por qualquer teste que use `_inbound()` padrão):

```python
async def test_billing_desabilitado_nao_manda_bloco_e_nao_debita(patched) -> None:
    patched["send"].return_value = {"responses": ["oi"], "tokens_used": 2000}

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert "end_customer_billing" not in patched["send"].await_args.kwargs
    patched["debitar_cliente_final"].assert_not_awaited()
```

Troque o último teste do arquivo (o único que ainda testava a ausência do payload como sinal de isenção — reescreva pra testar o que realmente importa, o roteamento de débito):

```python
async def test_contato_isento_nunca_e_customer_funded(patched) -> None:
    patched["load"].return_value = _inbound_com_billing(balance=1000, exempt=True)
    patched["send"].return_value = {"responses": ["oi"], "tokens_used": 2000}

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()
    assert "end_customer_billing" not in patched["send"].await_args.kwargs
```

por:

```python
async def test_contato_isento_nunca_e_customer_funded(patched) -> None:
    patched["load"].return_value = _inbound_com_billing(balance=1000, exempt=True)
    patched["send"].return_value = {"responses": ["oi"], "tokens_used": 2000}

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()
    patched["debitar_cliente_final"].assert_not_awaited()
    patched["debitar"].assert_awaited_once()
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd apps/worker && uv run pytest tests/unit/test_process_inbound_message.py -v`
Expected: FAIL só nos testes tocados neste step (o código de produção ainda monta `extra_kwargs["end_customer_billing"]`, então nada quebra ainda pelas remoções de asserção — mas nenhum teste novo falha por isso; a falha real só aparece depois do Step 3, quando o parâmetro deixar de existir em `send_message_to_agents`. Nesta altura, rode só pra confirmar que a suíte de testes já editada continua consistente com o código atual — deve passar ainda, já que o código de produção não mudou. Prossiga pro Step 3.)

- [ ] **Step 3: Remover a montagem do payload em `process_inbound_message`**

Em `apps/worker/app/tasks/messages.py`, troque:

```python
    access_token = decrypt_access_token(inbound.access_token_encrypted)

    extra_kwargs: dict = {}
    if inbound.end_customer_billing_enabled and not inbound.end_customer_billing_exempt:
        extra_kwargs["end_customer_billing"] = {
            "enabled": True,
            "balance": inbound.end_customer_balance,
            "packages": inbound.end_customer_packages,
        }

    try:
        result = await send_message_to_agents(
            http,
            tenant_id=tenant_id,
            contact_phone_number=inbound.contact_phone_number,
            message=inbound.message_content,
            phone_number_id=inbound.phone_number_id,
            access_token=access_token,
            agents=inbound.agents,
            **extra_kwargs,
        )
```

por:

```python
    access_token = decrypt_access_token(inbound.access_token_encrypted)

    try:
        result = await send_message_to_agents(
            http,
            tenant_id=tenant_id,
            contact_phone_number=inbound.contact_phone_number,
            message=inbound.message_content,
            phone_number_id=inbound.phone_number_id,
            access_token=access_token,
            agents=inbound.agents,
        )
```

- [ ] **Step 4: Remover o parâmetro em `send_message_to_agents`**

Em `apps/worker/app/clients/agents.py`, troque a função inteira:

```python
async def send_message_to_agents(
    http: httpx.AsyncClient,
    *,
    tenant_id: str,
    contact_phone_number: str,
    message: str,
    phone_number_id: str,
    access_token: str,
    end_customer_billing: dict | None = None,
    agents: list[dict] | None = None,
) -> dict | None:
    """Chama POST /messages do agents service.

    Retorna {"responses": [...], "tokens_used": N, "tokens_input": N,
    "tokens_output": N, "delivery_failures": [...]}, ou None quando o agents
    devolve 202 (a mensagem foi agrupada pelo debounce numa execução já em
    andamento — as respostas virão pela execução que está rodando).
    tokens_input/tokens_output valem 0 quando o agents ainda não devolve o
    breakdown (versão antiga durante o deploy).

    `end_customer_billing` (quando não None) leva {"enabled", "balance",
    "packages"} do cliente final — nenhum dado sensível, a secret key da
    Stripe do tenant nunca sai do api.

    `agents`: a lista de agentes do tenant (id, name, instructions,
    is_entry_point, knowledge_base_file_ids) — resolvida aqui a partir do
    Postgres do monorepo antes da chamada; o agents service nunca acessa
    esse banco diretamente.
    """
    headers = {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}
    payload = {
        "tenant_id": tenant_id,
        "contact_phone_number": contact_phone_number,
        "message": message,
        "attachments": [],
        "phone_number_id": phone_number_id,
        "access_token": access_token,
        "agents": agents or [],
    }
    if end_customer_billing is not None:
        # balance vem de end_customer_balances.credit_balance (Numeric(12,4)
        # desde a Etapa 1/2 da wallet unificada) — chega aqui como Decimal,
        # que o encoder JSON padrão não serializa. Converte na fronteira,
        # defensivo contra qualquer chamador (só há um hoje).
        payload["end_customer_billing"] = {
            **end_customer_billing,
            "balance": float(end_customer_billing["balance"]),
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

por:

```python
async def send_message_to_agents(
    http: httpx.AsyncClient,
    *,
    tenant_id: str,
    contact_phone_number: str,
    message: str,
    phone_number_id: str,
    access_token: str,
    agents: list[dict] | None = None,
) -> dict | None:
    """Chama POST /messages do agents service.

    Retorna {"responses": [...], "tokens_used": N, "tokens_input": N,
    "tokens_output": N, "delivery_failures": [...]}, ou None quando o agents
    devolve 202 (a mensagem foi agrupada pelo debounce numa execução já em
    andamento — as respostas virão pela execução que está rodando).
    tokens_input/tokens_output valem 0 quando o agents ainda não devolve o
    breakdown (versão antiga durante o deploy).

    `agents`: a lista de agentes do tenant (id, name, instructions,
    is_entry_point, knowledge_base_file_ids) — resolvida aqui a partir do
    Postgres do monorepo antes da chamada; o agents service nunca acessa
    esse banco diretamente.
    """
    headers = {"Authorization": settings.agents_api_key} if settings.agents_api_key else {}
    payload = {
        "tenant_id": tenant_id,
        "contact_phone_number": contact_phone_number,
        "message": message,
        "attachments": [],
        "phone_number_id": phone_number_id,
        "access_token": access_token,
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

- [ ] **Step 5: Rodar e confirmar sucesso**

Run: `cd apps/worker && uv run pytest tests/unit/test_process_inbound_message.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 6: Rodar a suíte completa do worker + lint**

Run: `cd apps/worker && uv run pytest tests/unit -q && uv run ruff check app/tasks/messages.py app/clients/agents.py`
Expected: tudo passa, sem erro novo.

- [ ] **Step 7: Commit**

```bash
git add apps/worker/app/tasks/messages.py apps/worker/app/clients/agents.py apps/worker/tests/unit/test_process_inbound_message.py
git commit -m "feat(worker): para de enviar end_customer_billing pro agents — o gate já intercepta tudo antes"
```

---

### Task 5: `apps/agents` — remove a tool `gerar_link_pagamento_cliente` e `is_billing_blocked`

**Files:**
- Delete: `apps/agents/clients/billing.py`
- Delete: `apps/agents/tests/unit/test_billing_client.py`
- Modify: `apps/agents/agents/tools.py`
- Modify: `apps/agents/tests/unit/test_tools.py`

**Interfaces:**
- Consumes: nada de outra task.
- Produces: `transfer_to_agent(agent_id, valid_agent_ids=None)` (assinatura simplificada, sem billing) e a ausência de `is_billing_blocked`/`gerar_link_pagamento_cliente` — consumidos pela Task 6.

- [ ] **Step 1: Apagar o client de billing e seu teste**

```bash
rm apps/agents/clients/billing.py apps/agents/tests/unit/test_billing_client.py
```

- [ ] **Step 2: Atualizar `test_tools.py` (TDD — remove os testes do que vai ser apagado, ajusta os de `transfer_to_agent`)**

Em `apps/agents/tests/unit/test_tools.py`, troque o bloco de import:

```python
from agents.tools import (
    transfer_to_agent,
    buscar_base_conhecimento_agente,
    bucar_base_conhecimento_usuario,
    enviar_documento,
    gerar_link_pagamento_cliente,
)
```

por:

```python
from agents.tools import (
    transfer_to_agent,
    buscar_base_conhecimento_agente,
    bucar_base_conhecimento_usuario,
    enviar_documento,
)
```

Troque os testes de billing de `transfer_to_agent`:

```python
def test_transfer_bloqueada_sem_saldo_retorna_string():
    result = transfer_to_agent.invoke(
        {
            "agent_id": "agent-2",
            "valid_agent_ids": ["agent-2"],
            "end_customer_billing_enabled": True,
            "end_customer_balance": 0,
        }
    )
    assert isinstance(result, str)
    assert "bloqueada" in result.lower()


def test_transfer_liberada_com_saldo_positivo():
    result = transfer_to_agent.invoke(
        {
            "agent_id": "agent-2",
            "valid_agent_ids": ["agent-2"],
            "end_customer_billing_enabled": True,
            "end_customer_balance": 100,
        }
    )
    assert isinstance(result, Command)
    assert result.update["current_agent_id"] == "agent-2"


def test_transfer_sem_billing_habilitado_ignora_saldo():
    result = transfer_to_agent.invoke(
        {
            "agent_id": "agent-2",
            "valid_agent_ids": ["agent-2"],
            "end_customer_billing_enabled": False,
            "end_customer_balance": 0,
        }
    )
    assert isinstance(result, Command)
```

por (remove os 3 — não há mais o que testar, `transfer_to_agent` nunca mais recebe billing):

(nada — apague o bloco inteiro)

Remova a seção inteira de `gerar_link_pagamento_cliente` no fim do arquivo:

```python
# ──────────────────────────────────────────────
# gerar_link_pagamento_cliente
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gerar_link_pagamento_divide_conversation_id_e_retorna_url():
    with patch(
        "agents.tools.criar_link_pagamento", new=AsyncMock(return_value="https://checkout.stripe.com/pay/cs_1")
    ) as mock_fn:
        result = await gerar_link_pagamento_cliente.ainvoke(
            {"package_id": "pkg-1", "conversation_id": "tenant-1:5511999998888"}
        )

        mock_fn.assert_called_once_with("tenant-1", "5511999998888", "pkg-1")
        assert "https://checkout.stripe.com/pay/cs_1" in result


@pytest.mark.asyncio
async def test_gerar_link_pagamento_falha_retorna_mensagem_amigavel():
    with patch("agents.tools.criar_link_pagamento", new=AsyncMock(return_value=None)):
        result = await gerar_link_pagamento_cliente.ainvoke(
            {"package_id": "pkg-1", "conversation_id": "tenant-1:5511999998888"}
        )

        assert "não foi possível" in result.lower()
```

por:

(nada — apague o bloco inteiro, incluindo o comentário de seção)

- [ ] **Step 3: Rodar e confirmar a falha**

Run: `cd apps/agents && python3 -m pytest tests/unit/test_tools.py -v`
Expected: FAIL com `ImportError`/`ModuleNotFoundError` — `agents.tools` ainda importa `criar_link_pagamento` de `clients.billing`, que você acabou de apagar.

- [ ] **Step 4: Remover a tool e `is_billing_blocked` de `agents/tools.py`**

Em `apps/agents/agents/tools.py`, troque o import:

```python
from langchain.tools import tool
from langgraph.types import Command
from clients.retrieval import retrieval_usuario, retrieval_escritorio
from clients.billing import criar_link_pagamento
from loguru import logger
```

por:

```python
from langchain.tools import tool
from langgraph.types import Command
from clients.retrieval import retrieval_usuario, retrieval_escritorio
from loguru import logger
```

Troque:

```python
@tool("gerar_link_pagamento_cliente")
async def gerar_link_pagamento_cliente(package_id: str, conversation_id: str) -> str:
    """Gera o link de pagamento (Stripe) pro cliente comprar um pacote de créditos.

    Use quando o cliente não tiver saldo suficiente pra continuar sendo
    atendido por um especialista, ou quando ele pedir explicitamente pra
    comprar mais créditos. Escolha o package_id entre os pacotes informados
    no seu contexto — nunca invente um id.

    Args:
        package_id: id do pacote escolhido (vem da lista de pacotes disponíveis).
        conversation_id: preenchido automaticamente pelo sistema.
    """
    tenant_id, _, contact_phone_number = str(conversation_id).partition(":")
    checkout_url = await criar_link_pagamento(tenant_id, contact_phone_number, package_id)
    if checkout_url is None:
        return (
            "Não foi possível gerar o link de pagamento agora — peça pro cliente "
            "tentar de novo em instantes."
        )
    return f"Link de pagamento gerado: {checkout_url}"

def is_billing_blocked(enabled: bool, balance: float) -> bool:
    """Bloqueia oferta/transferência quando a cobrança do cliente final está
    ativa e o saldo está zerado — usada tanto pelo gate técnico em
    transfer_to_agent quanto pela decisão de injetar os pacotes/pular a
    despedida em agente_secretaria, pra nunca divergir entre os dois."""
    return bool(enabled) and balance <= 0


@tool("transfer_to_agent")
def transfer_to_agent(
    agent_id: str,
    valid_agent_ids: list[str] | None = None,
    end_customer_billing_enabled: bool = False,
    end_customer_balance: float = 0,
) -> str:
    """
    Transfere a conversa para outro agente do escritório.

    Args:
        agent_id: id do agente de destino — escolha entre os agentes
            disponíveis no seu contexto, nunca invente um id.
        valid_agent_ids: preenchido automaticamente pelo sistema.
        end_customer_billing_enabled: preenchido automaticamente pelo sistema.
        end_customer_balance: preenchido automaticamente pelo sistema.
    """
    if agent_id not in (valid_agent_ids or []):
        return (
            "Transferência recusada: agent_id inválido — escolha um dos agentes "
            "disponíveis no seu contexto."
        )
    if is_billing_blocked(end_customer_billing_enabled, end_customer_balance):
        return (
            "Transferência bloqueada: o cliente ainda não tem créditos disponíveis. "
            "Ofereça os pacotes de crédito e gere o link de pagamento antes de "
            "transferir para outro agente."
        )
    return Command(
        update={
            "current_agent_id": agent_id,
            "receptive_message_specialist": True,
        }
    )



tools = [
    buscar_base_conhecimento_agente,
    bucar_base_conhecimento_usuario,
    gerar_link_pagamento_cliente,
    transfer_to_agent,
]
```

por:

```python
@tool("transfer_to_agent")
def transfer_to_agent(agent_id: str, valid_agent_ids: list[str] | None = None) -> str:
    """
    Transfere a conversa para outro agente do escritório.

    Args:
        agent_id: id do agente de destino — escolha entre os agentes
            disponíveis no seu contexto, nunca invente um id.
        valid_agent_ids: preenchido automaticamente pelo sistema.
    """
    if agent_id not in (valid_agent_ids or []):
        return (
            "Transferência recusada: agent_id inválido — escolha um dos agentes "
            "disponíveis no seu contexto."
        )
    return Command(
        update={
            "current_agent_id": agent_id,
            "receptive_message_specialist": True,
        }
    )


tools = [
    buscar_base_conhecimento_agente,
    bucar_base_conhecimento_usuario,
    transfer_to_agent,
]
```

- [ ] **Step 5: Rodar e confirmar sucesso**

Run: `cd apps/agents && python3 -m pytest tests/unit/test_tools.py -v`
Expected: todos os testes do arquivo passam (os 5 testes de `transfer_to_agent` que restaram — `retorna_command`, `atualiza_current_agent_id`, `ativa_receptive_message`, `agent_id_fora_da_lista_recusa`, `sem_valid_agent_ids_recusa` — nenhum deles passava `end_customer_billing_enabled`/`end_customer_balance`, então continuam idênticos).

- [ ] **Step 6: Rodar a suíte completa + lint**

Run: `cd apps/agents && python3 -m pytest tests/unit -q && python3 -m ruff check agents/tools.py 2>&1 | head -30`
Expected: `test_tools.py`/`test_billing_client.py` (apagado) ok; espere falhas em OUTROS arquivos (`test_nodes.py`, `test_routes.py`) que ainda referenciam `gerar_link_pagamento_cliente`/`end_customer_billing` — essas são resolvidas nas Tasks 6-7, não se preocupe com elas agora. Ruff: ignore débito pré-existente já documentado no `CLAUDE.md`, só confirme que não há erro NOVO especificamente em `agents/tools.py`.

- [ ] **Step 7: Commit**

```bash
git add -A apps/agents/clients/billing.py apps/agents/tests/unit/test_billing_client.py apps/agents/agents/tools.py apps/agents/tests/unit/test_tools.py
git commit -m "feat(agents): remove gerar_link_pagamento_cliente e is_billing_blocked — mecanismo antigo aposentado"
```

---

### Task 6: `apps/agents` — `agent_node`/`tool_node` sem gate de billing

**Files:**
- Modify: `apps/agents/agents/nodes.py`
- Modify: `apps/agents/agents/workflow.py`
- Modify: `apps/agents/tests/unit/test_nodes.py`

**Interfaces:**
- Consumes: `transfer_to_agent` simplificada (Task 5).
- Produces: `State` sem `end_customer_billing` — consumido pela Task 7.

- [ ] **Step 1: Atualizar `test_nodes.py` (TDD — remove os testes do comportamento apagado)**

Em `apps/agents/tests/unit/test_nodes.py`, remova os 3 testes de binding condicional da tool:

```python
@pytest.mark.asyncio
async def test_bind_inclui_gerar_link_pagamento_quando_billing_habilitado(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agent_node(base_state(end_customer_billing={"enabled": True, "balance": 500, "packages": []}))

    bound_tools = model.bind_tools.call_args.args[0]
    tool_names = {t.name for t in bound_tools}
    assert "gerar_link_pagamento_cliente" in tool_names


@pytest.mark.asyncio
async def test_bind_nao_inclui_gerar_link_pagamento_quando_billing_desabilitado(monkeypatch) -> None:
    """A mera presença da tool no bind_tools já muda o comportamento de
    function-calling do modelo (visto num teste de integração real) — por
    isso ela só entra na lista quando a feature está de fato ligada pro
    tenant, nunca incondicionalmente."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agent_node(base_state(end_customer_billing={"enabled": False, "balance": 0, "packages": []}))

    bound_tools = model.bind_tools.call_args.args[0]
    tool_names = {t.name for t in bound_tools}
    assert "gerar_link_pagamento_cliente" not in tool_names


@pytest.mark.asyncio
async def test_bind_nao_inclui_gerar_link_pagamento_sem_end_customer_billing_no_state(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agent_node(base_state())

    bound_tools = model.bind_tools.call_args.args[0]
    tool_names = {t.name for t in bound_tools}
    assert "gerar_link_pagamento_cliente" not in tool_names
```

Remova os 6 testes de injeção de prompt/pacotes/aviso de retorno:

```python
@pytest.mark.asyncio
async def test_injeta_pacotes_no_prompt_quando_sem_saldo_no_ponto_de_entrada(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": True,
            "balance": 0,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agent_node(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" in prompt_arg.content
    assert "p-1" in prompt_arg.content


@pytest.mark.asyncio
async def test_instrui_a_nao_revelar_package_id_ao_cliente(monkeypatch) -> None:
    """Bug real reportado pelo usuário: a secretária repetiu o package_id
    (um uuid grande) na mensagem pro cliente, porque a instrução nunca
    dizia que esse id é só de uso interno."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": True,
            "balance": 0,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agent_node(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "NUNCA mencione o package_id ao cliente" in prompt_arg.content


@pytest.mark.asyncio
async def test_instrui_a_colar_o_link_retornado_na_resposta(monkeypatch) -> None:
    """Bug real reportado pelo usuário: a secretária disse 'gerei o link de
    pagamento' sem colar o link de verdade, porque a instrução nunca dizia
    explicitamente pra copiar o retorno da tool na resposta ao cliente."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": True,
            "balance": 0,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agent_node(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "copie esse link literalmente na sua resposta ao cliente" in prompt_arg.content


@pytest.mark.asyncio
async def test_nao_injeta_pacotes_quando_billing_desabilitado(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": False,
            "balance": 0,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agent_node(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" not in prompt_arg.content
    assert "Pacotes disponíveis" not in prompt_arg.content


@pytest.mark.asyncio
async def test_nao_injeta_pacotes_com_saldo_positivo(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": True,
            "balance": 500,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agent_node(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" not in prompt_arg.content
```

(remova cada um desses 5 testes inteiros — do `@pytest.mark.asyncio` até o fim do corpo da função, mantendo o comentário de seção `# agent_node — roster de outros agentes...` que vem antes deles intacto, já que ele continua valendo pros testes de roster que sobram.)

Remova:

```python
@pytest.mark.asyncio
async def test_transfer_sem_content_pula_despedida_quando_bloqueado():
    """Quando a transferência vai ser bloqueada (sem saldo), não injeta despedida —
    o tool_node ainda vai rodar e transfer_to_agent vai recusar, então a despedida
    ("vou te passar agora") ficaria contraditória."""
    from agents.nodes import agent_node
    fake = ai_with_tool_call("transfer_to_agent", {"agent_id": "other-1"}, content="")

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(
            base_state(end_customer_billing={"enabled": True, "balance": 0, "packages": []})
        )

    ai_msg = result.update["messages"][0]
    assert ai_msg.content == ""
    assert ai_msg.tool_calls, "tool_calls devem ser preservados mesmo sem despedida"
    assert result.goto == "tool_node"
```

Remova os 4 testes de saldo esgotado/aviso de retorno/bounce:

```python
@pytest.mark.asyncio
async def test_saldo_esgotado_no_meio_da_conversa_devolve_pro_ponto_de_entrada(monkeypatch) -> None:
    """Saldo esgotado no meio da conversa (não só na transferência inicial) deve
    ser atendido pelo ponto de entrada (equivalente à antiga secretária), que
    oferece os pacotes — em vez de deixar o agente atual responder de graça."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("aqui estão os pacotes disponíveis"))
    monkeypatch.setattr("agents.nodes.model", model)

    result = await agent_node(
        base_state(
            current_agent_id="other-1",
            receptive_message_specialist=False,
            end_customer_billing={
                "enabled": True,
                "balance": 0,
                "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
            },
        )
    )

    assert result.update["current_agent_id"] == "entry-1"
    model.bind_tools.assert_called_once()
    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" in prompt_arg.content

    # Aviso fixo de retorno vem antes da resposta normal do ponto de entrada.
    assert len(result.update["messages"]) == 2
    aviso, resposta = result.update["messages"]
    assert aviso.content == (
        "voltando para Secretária — o atendimento anterior ficou indisponível "
        "porque os créditos acabaram."
    )
    assert resposta.content == "aqui estão os pacotes disponíveis"


@pytest.mark.asyncio
async def test_aviso_de_retorno_nao_repete_quando_ja_esta_no_ponto_de_entrada(monkeypatch) -> None:
    """O aviso de retorno só deve aparecer no turno exato da transição
    especialista -> ponto de entrada. Nos turnos seguintes, com
    current_agent_id já apontando pro ponto de entrada, a condição de
    bloqueio (`not current["is_entry_point"]`) nunca mais é verdadeira —
    então o aviso não deve se repetir."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("aqui estão os pacotes disponíveis"))
    monkeypatch.setattr("agents.nodes.model", model)

    result = await agent_node(
        base_state(
            current_agent_id="entry-1",
            receptive_message_specialist=False,
            end_customer_billing={
                "enabled": True,
                "balance": 0,
                "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
            },
        )
    )

    assert result.update["current_agent_id"] == "entry-1"
    assert len(result.update["messages"]) == 1
    assert result.update["messages"][0].content == "aqui estão os pacotes disponíveis"


@pytest.mark.asyncio
async def test_agente_com_saldo_positivo_nao_e_bloqueado():
    """Billing habilitado mas com saldo positivo não deve bloquear — fluxo normal."""
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response("Analisando seu caso."))):
        result = await agent_node(
            base_state(
                current_agent_id="other-1",
                receptive_message_specialist=False,
                end_customer_billing={"enabled": True, "balance": 500, "packages": []},
            )
        )

    assert result.goto == END
    assert result.update["current_agent_id"] == "other-1"
    assert result.update["messages"][0].content == "Analisando seu caso."


@pytest.mark.asyncio
async def test_agente_sem_billing_no_state_nao_bloqueia():
    """Sem end_customer_billing no state (fluxo normal de escritório, sem
    cobrança de cliente final), o agente segue chamando o LLM normalmente."""
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response("Vou orientar você."))):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=False))

    assert result.goto == END
    assert result.update["messages"][0].content == "Vou orientar você."
```

(remova cada um inteiro — mantenha `test_transfer_sem_content_injeta_despedida_no_agente_atual`, que vem depois e não usa billing.)

Troque `test_tool_node_injeta_saldo_do_cliente_final_em_transfer_to_agent` e `test_tool_node_sem_end_customer_billing_no_state_nao_bloqueia`:

```python
@pytest.mark.asyncio
async def test_tool_node_injeta_saldo_do_cliente_final_em_transfer_to_agent() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "transfer_to_agent",
                # O LLM tentou passar saldo positivo — deve ser ignorado.
                "args": {"agent_id": "other-1", "end_customer_balance": 9999},
                "id": "call-1",
            }
        ],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-1:5511999998888",
        "agents": base_state()["agents"],
        "end_customer_billing": {"enabled": True, "balance": 0, "packages": []},
    }

    result = await tool_node(state)

    assert "bloqueada" in result["messages"][0].content.lower()


@pytest.mark.asyncio
async def test_tool_node_sem_end_customer_billing_no_state_nao_bloqueia() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[{"name": "transfer_to_agent", "args": {"agent_id": "other-1"}, "id": "call-1"}],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-1:5511999998888",
        "agents": base_state()["agents"],
    }

    result = await tool_node(state)

    assert result.get("current_agent_id") == "other-1"
```

por (remova o primeiro inteiro — não há mais gate de billing no `transfer_to_agent` pra testar; mantenha o segundo, que já cobre o caminho normal de transferência):

```python
@pytest.mark.asyncio
async def test_tool_node_transfer_sem_billing_no_state_funciona_normalmente() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[{"name": "transfer_to_agent", "args": {"agent_id": "other-1"}, "id": "call-1"}],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-1:5511999998888",
        "agents": base_state()["agents"],
    }

    result = await tool_node(state)

    assert result.get("current_agent_id") == "other-1"
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd apps/agents && python3 -m pytest tests/unit/test_nodes.py -v`
Expected: FAIL — o `agent_node`/`tool_node` de produção ainda referenciam `is_billing_blocked`, que a Task 5 já apagou de `agents/tools.py` — o arquivo inteiro falha na coleta (`ImportError`) até o Step 3 ser aplicado.

- [ ] **Step 3: Simplificar `agent_node`/`tool_node`**

Em `apps/agents/agents/nodes.py`, troque:

```python
# Tools cujo conversation_id vem SEMPRE do estado do grafo, nunca do LLM —
# o tenant_id vive dentro dele (isolamento multi-tenant).
STATE_SCOPED_TOOLS = {
    "bucar_base_conhecimento_usuario",
    "buscar_base_conhecimento_agente",
    "gerar_link_pagamento_cliente",
}
# Saldo/enabled do cliente final: nunca confiar em valor vindo do LLM.
BILLING_GATED_TOOLS = {"transfer_to_agent"}
```

por:

```python
# Tools cujo conversation_id vem SEMPRE do estado do grafo, nunca do LLM —
# o tenant_id vive dentro dele (isolamento multi-tenant).
STATE_SCOPED_TOOLS = {
    "bucar_base_conhecimento_usuario",
    "buscar_base_conhecimento_agente",
}
```

Troque:

```python
    current_agent_id = state.get("current_agent_id")
    current = agents_by_id.get(current_agent_id) if current_agent_id else None
    if current is None:
        current = entry_point

    billing = state.get("end_customer_billing") or {}
    billing_enabled = bool(billing.get("enabled"))
    billing_blocked = is_billing_blocked(billing.get("enabled"), billing.get("balance", 0))

    bounced_from_billing_block = False
    if billing_blocked and not current["is_entry_point"]:
        logger.info(
            "Agente bloqueado por saldo esgotado, devolvendo pro ponto de entrada | agent_id={}",
            current["id"],
        )
        current = entry_point
        bounced_from_billing_block = True

    is_entry_point = current["is_entry_point"]
```

por:

```python
    current_agent_id = state.get("current_agent_id")
    current = agents_by_id.get(current_agent_id) if current_agent_id else None
    if current is None:
        current = entry_point

    is_entry_point = current["is_entry_point"]
```

Troque:

```python
    last_messages = strip_messages(state["messages"], state["num_before_messages"])

    # gerar_link_pagamento_cliente só é bindada quando a cobrança do cliente
    # final está de fato habilitada pro tenant — do contrário, a mera presença
    # da tool na lista já muda o comportamento de function-calling do modelo
    # (verificado num teste de integração real: o modelo passou a pedir uma
    # pergunta de esclarecimento antes de transferir mesmo sem a feature
    # habilitada, só por ter uma tool a mais disponível).
    tools_for_agent = [transfer_to_agent, buscar_base_conhecimento_agente, bucar_base_conhecimento_usuario]
    if billing_enabled:
        tools_for_agent.append(gerar_link_pagamento_cliente)
    model_with_tools = model.bind_tools(tools_for_agent)
```

por:

```python
    last_messages = strip_messages(state["messages"], state["num_before_messages"])

    tools_for_agent = [transfer_to_agent, buscar_base_conhecimento_agente, bucar_base_conhecimento_usuario]
    model_with_tools = model.bind_tools(tools_for_agent)
```

Troque:

```python
    if billing_blocked and is_entry_point:
        packages_text = "\n".join(
            f"- {p['name']}: R$ {p['price_brl']} = {p['credits_granted']} créditos "
            f"(package_id: {p['id']})"
            for p in billing.get("packages", [])
        )
        prompt += (
            "\n\n---\n"
            "**Instrução:** Este cliente está sem créditos disponíveis. Antes de "
            "transferir para outro agente, explique que é necessário comprar "
            "créditos e ofereça os pacotes abaixo — descreva cada pacote só pelo "
            "nome, preço e créditos. NUNCA mencione o package_id ao cliente: é um "
            "identificador interno, só pra você usar ao chamar a tool. Quando o "
            "cliente escolher um, use a tool gerar_link_pagamento_cliente com o "
            "package_id correspondente. A tool devolve o link de pagamento no "
            "resultado — copie esse link literalmente na sua resposta ao cliente; "
            "nunca diga apenas que gerou o link sem mostrá-lo. Depois que o "
            "cliente confirmar que pagou, chame transfer_to_agent de novo — é essa "
            "chamada que efetivamente libera o outro agente; nunca diga que já "
            "transferiu sem chamar essa ferramenta.\n\n"
            f"Pacotes disponíveis:\n{packages_text}"
        )
    if is_first_run:
```

por:

```python
    if is_first_run:
```

Troque:

```python
    update: dict = {"messages": [response], "current_agent_id": current["id"]}
    if is_first_run:
        update["receptive_message_specialist"] = False
    if bounced_from_billing_block:
        aviso_retorno = AIMessage(
            content=(
                f"voltando para {entry_point['name']} — o atendimento anterior "
                "ficou indisponível porque os créditos acabaram."
            )
        )
        update["messages"] = [aviso_retorno, response]
        logger.info("Aviso de retorno ao ponto de entrada injetado | entry_point_id={}", entry_point["id"])

    if response.tool_calls:
        tool_name = response.tool_calls[0]["name"]
        logger.info("Ferramenta selecionada | tool={}", tool_name)

        if tool_name == "transfer_to_agent" and not response.content and not billing_blocked:
```

por:

```python
    update: dict = {"messages": [response], "current_agent_id": current["id"]}
    if is_first_run:
        update["receptive_message_specialist"] = False

    if response.tool_calls:
        tool_name = response.tool_calls[0]["name"]
        logger.info("Ferramenta selecionada | tool={}", tool_name)

        if tool_name == "transfer_to_agent" and not response.content:
```

Troque:

```python
        if tool_call["name"] == "transfer_to_agent":
            args["valid_agent_ids"] = list(agents_by_id.keys())
        if tool_call["name"] in BILLING_GATED_TOOLS:
            billing = state.get("end_customer_billing") or {}
            args["end_customer_billing_enabled"] = bool(billing.get("enabled"))
            args["end_customer_balance"] = billing.get("balance", 0)

        logger.info("Executando ferramenta | tool={} | args={}", tool_call["name"], args)
```

por:

```python
        if tool_call["name"] == "transfer_to_agent":
            args["valid_agent_ids"] = list(agents_by_id.keys())

        logger.info("Executando ferramenta | tool={} | args={}", tool_call["name"], args)
```

- [ ] **Step 4: Remover `end_customer_billing` de `State`**

Em `apps/agents/agents/workflow.py`, troque:

```python
class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    attachments: list
    conversation_id: str
    num_before_messages: int
    current_agent_id: str | None
    receptive_message_specialist: bool
    end_customer_billing: dict | None
    agents: list[dict]
```

por:

```python
class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    attachments: list
    conversation_id: str
    num_before_messages: int
    current_agent_id: str | None
    receptive_message_specialist: bool
    agents: list[dict]
```

- [ ] **Step 5: Rodar e confirmar sucesso**

Run: `cd apps/agents && python3 -m pytest tests/unit/test_nodes.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 6: Rodar a suíte completa + lint**

Run: `cd apps/agents && python3 -m pytest tests/unit -q`
Expected: só `test_routes.py` ainda deve falhar (referencia `end_customer_billing` no schema/`run_agent` — resolvido na Task 7). Confirme que `test_nodes.py`/`test_tools.py` estão 100% verdes.

- [ ] **Step 7: Commit**

```bash
git add apps/agents/agents/nodes.py apps/agents/agents/workflow.py apps/agents/tests/unit/test_nodes.py
git commit -m "feat(agents): remove o gate de billing de agent_node/tool_node — sem bounce, sem prompt de pacotes, sem aviso de retorno"
```

---

### Task 7: `apps/agents` — remove `end_customer_billing` do contrato (`IncomingMessage`/`run_agent`)

**Files:**
- Modify: `apps/agents/api/routes.py`
- Modify: `apps/agents/services/call_agent.py`
- Modify: `apps/agents/tests/unit/test_routes.py`

**Interfaces:**
- Consumes: `State` sem `end_customer_billing` (Task 6).
- Produces: nada consumido por outra task deste plano — fecha a remoção no lado do `apps/agents`.

- [ ] **Step 1: Atualizar `test_routes.py` (TDD)**

Em `apps/agents/tests/unit/test_routes.py`, remova os 2 testes inteiros:

```python
def test_end_customer_billing_e_repassado_ao_run_agent(client, monkeypatch):
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(
        return_value=(
            ["oi"],
            {"input_tokens": 70, "output_tokens": 30, "total_tokens": 100},
            "agente_secretaria",
        )
    )
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    _mock_whatsapp_client(monkeypatch)

    billing = {
        "enabled": True,
        "balance": 0,
        "packages": [{"id": "p-1", "name": "Básico"}],
    }
    payload = {**PAYLOAD, "end_customer_billing": billing}

    response = client.post("/messages", json=payload)

    assert response.status_code == 200
    assert run_agent.call_args.kwargs["end_customer_billing"] == billing


def test_sem_end_customer_billing_repassa_none(client, monkeypatch):
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(
        return_value=(
            ["oi"],
            {"input_tokens": 70, "output_tokens": 30, "total_tokens": 100},
            "agente_secretaria",
        )
    )
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    _mock_whatsapp_client(monkeypatch)

    response = client.post("/messages", json=PAYLOAD)

    assert response.status_code == 200
    assert run_agent.call_args.kwargs["end_customer_billing"] is None
```

por:

(nada — apague as 2 funções inteiras, incluindo esta linha final que fecha `test_sem_end_customer_billing_repassa_none`)

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd apps/agents && python3 -m pytest tests/unit/test_routes.py -v`
Expected: os 2 testes removidos não existem mais pra falhar — mas a suíte inteira ainda deve estar verde nesse ponto (o Step 1 só remove testes, não muda produção). Rode pra confirmar baseline antes do Step 3: `python3 -m pytest tests/unit/test_routes.py -v` deve passar 100% ainda, já que `IncomingMessage.end_customer_billing` continua existindo (só não é mais testado). Prossiga pro Step 3 pra remover o campo de produção — só ENTÃO um teste hipotético que ainda referenciasse `end_customer_billing` falharia; como você já removeu os 2 únicos, não há falha esperada aqui, e isso é o comportamento correto (a remoção de teste precede a remoção de produção quando o teste cobre exclusivamente algo que vai deixar de existir).

- [ ] **Step 3: Remover o campo do schema e do fluxo**

Em `apps/agents/api/routes.py`, troque:

```python
    tenant_id: str
    contact_phone_number: str
    message: str = ""
    attachments: list = Field(default_factory=list)
    phone_number_id: str = ""
    access_token: str = ""
    send_to_whatsapp: bool = True
    end_customer_billing: dict | None = None
    agents: list[dict] = Field(default_factory=list)
```

por:

```python
    tenant_id: str
    contact_phone_number: str
    message: str = ""
    attachments: list = Field(default_factory=list)
    phone_number_id: str = ""
    access_token: str = ""
    send_to_whatsapp: bool = True
    agents: list[dict] = Field(default_factory=list)
```

Troque:

```python
        response, usage, current_agent = await run_agent(
            message=messages["combined_message"],
            attachments=body.attachments,
            conversation_id=thread_id,
            number_whatsapp=body.contact_phone_number,
            end_customer_billing=body.end_customer_billing,
            agents=body.agents,
        )
```

por:

```python
        response, usage, current_agent = await run_agent(
            message=messages["combined_message"],
            attachments=body.attachments,
            conversation_id=thread_id,
            number_whatsapp=body.contact_phone_number,
            agents=body.agents,
        )
```

Em `apps/agents/services/call_agent.py`, troque:

```python
async def run_agent(
    message: str,
    conversation_id: str,
    attachments: list = [],
    number_whatsapp: str | None = None,
    db_uri: str = DB_URI,
    num_before_messages: int = 35,
    extra_data: dict = {},
    end_customer_billing: dict | None = None,
    agents: list[dict] | None = None,
) -> tuple[list[str], dict, str | None]:
```

por:

```python
async def run_agent(
    message: str,
    conversation_id: str,
    attachments: list = [],
    number_whatsapp: str | None = None,
    db_uri: str = DB_URI,
    num_before_messages: int = 35,
    extra_data: dict = {},
    agents: list[dict] | None = None,
) -> tuple[list[str], dict, str | None]:
```

Troque:

```python
        response = await agent.ainvoke(
            {
                "messages": [HumanMessage(content=message)],
                "attachments": attachments,
                "conversation_id": conversation_id,
                "num_before_messages": num_before_messages,
                "end_customer_billing": end_customer_billing,
                "agents": agents,
            },
            config=config,
        )
```

por:

```python
        response = await agent.ainvoke(
            {
                "messages": [HumanMessage(content=message)],
                "attachments": attachments,
                "conversation_id": conversation_id,
                "num_before_messages": num_before_messages,
                "agents": agents,
            },
            config=config,
        )
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/agents && python3 -m pytest tests/unit/test_routes.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 5: Rodar a suíte completa + lint**

Run: `cd apps/agents && python3 -m pytest tests/unit -q`
Expected: suíte inteira verde (as únicas exclusões esperadas em relação ao estado anterior à Task 5 são os testes/arquivos apagados nas Tasks 5-7 — nenhuma falha nova).

- [ ] **Step 6: Commit**

```bash
git add apps/agents/api/routes.py apps/agents/services/call_agent.py apps/agents/tests/unit/test_routes.py
git commit -m "feat(agents): remove end_customer_billing do contrato IncomingMessage/run_agent/State"
```

---

### Task 8: Documentação (`CLAUDE.md` + `apps/agents/API_AGENTS.md`)

**Files:**
- Modify: `CLAUDE.md`
- Modify: `apps/agents/API_AGENTS.md`

**Interfaces:**
- Consumes: nada (documentação, sem código).
- Produces: nada — última task do plano.

- [ ] **Step 1: `CLAUDE.md` — Modelo de Dados**

Troque:

```markdown
- `insufficient_balance_policy` (`block_with_message` [default] | `deterministic_gate` — rollout gradual por tenant do billing gate determinístico, ver seção "Cobrança do cliente final"; coluna `String` sem CHECK constraint, então o valor novo não exigiu migração)
- `billing_gate_welcome_text` (nullable — texto de boas-vindas customizado pelo tenant pro billing gate; cai num texto institucional padrão se não configurado)
```

por:

```markdown
- `billing_gate_welcome_text` (nullable — texto de boas-vindas customizado pelo tenant pro billing gate; cai num texto institucional padrão se não configurado)
```

- [ ] **Step 2: `CLAUDE.md` — seção "Cobrança do cliente final"**

Troque:

```markdown
Além do billing tenant→plataforma (acima), cada tenant pode cobrar os **próprios clientes finais** pelo uso dos agentes no WhatsApp, usando a **conta Stripe do próprio tenant** — completamente independente da Stripe da plataforma. Mesmo modelo de créditos, dois ledgers/saldos separados — mas ✅ **moeda única (implementada)**: uma execução do agente nunca debita os dois ao mesmo tempo. Quando a cobrança está habilitada **e** o cliente final tinha saldo positivo antes da chamada, o turno é **custeado só pela wallet do cliente final**; senão, é custeado só pelo estoque do tenant com a plataforma (regra da seção "Regra de consumo" acima) — a lógica é a mesma tanto no `worker` (mensagem real de WhatsApp) quanto no `agents` (gate de transferência).
```

por:

```markdown
Além do billing tenant→plataforma (acima), cada tenant pode cobrar os **próprios clientes finais** pelo uso dos agentes no WhatsApp, usando a **conta Stripe do próprio tenant** — completamente independente da Stripe da plataforma. Mesmo modelo de créditos, dois ledgers/saldos separados — mas ✅ **moeda única (implementada)**: uma execução do agente nunca debita os dois ao mesmo tempo. Quando a cobrança está habilitada **e** o cliente final tinha saldo positivo antes da chamada, o turno é **custeado só pela wallet do cliente final**; senão, é custeado só pelo estoque do tenant com a plataforma (regra da seção "Regra de consumo" acima) — decidido inteiramente no `worker`, antes de qualquer chamada ao `agents`.
```

Troque:

```markdown
- **Gate técnico no grafo do `agents`**: sem saldo (feature habilitada + `balance <= 0`), a tool `transfer_to_agent` recusa a transferência e o ponto de entrada oferece os pacotes cadastrados/gera o link em vez de transferir. Esse saldo é **re-checado a cada turno dentro do `agent_node` genérico também**, não só na transferência inicial — sem isso, uma vez transferida a conversa fica fixada no agente de destino (`current_agent_id` no checkpoint), então um cliente que comprasse um pacote pequeno ganharia atendimento gratuito ilimitado depois de esgotar o saldo. Quando bloqueado, o agente que não é o ponto de entrada é atendido pelo próprio ponto de entrada no mesmo turno, em vez de responder — com um aviso fixo explicando o motivo do retorno, disparado uma única vez por bloqueio (ver seção Agents Service).
- ✅ **Moeda única no `worker`**: `process_inbound_message` lê o saldo do cliente final antes de chamar o `agents` (como já fazia) e decide `customer_funded = enabled and balance > 0` — se `True`, debita **só** `end_customer_balances` (créditos ponderados, `calcular_creditos`, com lock e auditoria); se `False`, debita **só** `tenants.credit_balance`. O gate de saldo esgotado do tenant (`credit_balance <= 0` → silêncio total) **não dispara** quando `customer_funded` é `True` — o turno roda mesmo com o estoque do tenant zerado, porque esse crédito específico não sai mais dali.
- **`insufficient_balance_policy`** (`tenant_billing_settings`, migration `0014`): `block_with_message` (default, comportamento acima) | `deterministic_gate` (✅ implementado, migration `0018` — ver "Billing gate determinístico" abaixo). Coluna `String` sem CHECK constraint — migrar um tenant é um `UPDATE` direto no banco, sem deploy novo.
- **`/pagamento-confirmado`**: página pública e estática do `web` (sem sessão, sem polling) — destino do `success_url`/`cancel_url` do checkout do cliente final; a confirmação de fato chega pelo WhatsApp via o webhook acima.

#### Billing gate determinístico — ✅ implementado (rollout gradual, coexiste com o gate acima)

> `docs/superpowers/specs/2026-07-22-billing-gate-deterministico-design.md` / `docs/superpowers/plans/2026-07-22-billing-gate-implementacao.md`

Pra tenants migrados pra `insufficient_balance_policy = "deterministic_gate"`, o funil "sem saldo → escolher pacote → pagar → liberado" deixou de passar pelo `agents` (LLM) e virou uma máquina de estados determinística, inteiramente no `worker` (`apps/worker/app/billing_gate.py`), usando mensagens nativas do WhatsApp (`interactive`/`list`) — zero custo de LLM nesse trecho. `conversations` ganhou o terceiro estado `billing_gate` + `billing_gate_step`/`billing_gate_retries`/`billing_gate_checkout_url` (ver Modelo de Dados).

- **Entrada** (`maybe_enter_gate`, checado a cada mensagem em `process_inbound_message`, antes do fluxo normal): tenant com a policy nova + cobrança habilitada + saldo do contato `<= 0` → transiciona `agent`→`billing_gate`. Tenants em `block_with_message` (default) nunca entram aqui — o gate antigo dentro do `agents` continua valendo pra eles, sem nenhuma mudança.
- **Steps**: `null` (abre o gate — manda texto de boas-vindas + lista interativa de pacotes) → `aguardando_selecao_pacote` (resolve a seleção **pelo título/nome do pacote**, já que o parser do webhook (`extract_inbound_messages`) persiste a resposta de uma lista como `title`, não `id`, e `messages.content` não guarda o `message_type` original; gera o link de pagamento via `POST /internal/end-customer-billing/checkout` e armazena em `billing_gate_checkout_url`) → `aguardando_pagamento` (reenvia o link já armazenado — nunca gera um novo).
- **Retries**: reseta a 0 a cada mudança de step, incrementa só numa resposta não reconhecida dentro do mesmo step; em `MAX_RETRIES = 3` sem sucesso, escala pra `state = "human"`.
- **Falha de envio (WhatsApp/Stripe) dentro do gate**: escala pra `human` também (não só resposta não reconhecida) — sem isso, uma falha de rede deixaria a conversa travada em `billing_gate` pra sempre, já que a válvula de escape por retry só cobria respostas, não exceções.
- **Fechamento do ciclo**: o webhook Stripe do tenant (`process_end_customer_checkout_completed` → `_send_purchase_confirmation`, `apps/api/app/services/end_customer_billing.py`) agora ramifica por `insufficient_balance_policy` — `deterministic_gate` transiciona a conversa direto de `billing_gate` pra `agent` (sem acionar o `agents`); `block_with_message` preserva o mecanismo antigo intacto (mensagem de gatilho + `arq.enqueue_job("process_inbound_message", ...)`, que faz a secretária "notar" o pagamento).
- **Fora de escopo desta etapa**: `apps/web` não tem UI dedicada pro estado `billing_gate` (uma conversa nesse estado aparece como "não humana" no painel); migração de tenant pra essa policy é operacional (`UPDATE` direto), sem self-service; remoção do gate antigo do `agents` só acontece depois de 100% dos tenants migrados.
- ✅ **Auto-recuperação de uma corrida rara com a isenção de cobrança** (ver subseção abaixo): se o tenant isenta um contato no instante exato entre o `worker` ler o contexto (`_load_context`) e comitar a entrada no gate, a conversa pode ficar `state="billing_gate"` com `end_customer_billing_exempt=true` ao mesmo tempo. `maybe_enter_gate` detecta essa combinação na mensagem seguinte (o próprio topo da função, antes de qualquer outra checagem) e sai do gate na hora (`state="agent"`, reset de step/retries) em vez de perpetuar o bloqueio — sem isso, o curto-circuito de reentrada (`state == "billing_gate"` → `return True`) nunca chegaria a checar a isenção.
```

por:

```markdown
- ✅ **Moeda única no `worker`**: `process_inbound_message` lê o saldo do cliente final antes de chamar o `agents` e decide `customer_funded = enabled and balance > 0` — se `True`, debita **só** `end_customer_balances` (créditos ponderados, `calcular_creditos`, com lock e auditoria); se `False`, debita **só** `tenants.credit_balance`. O gate de saldo esgotado do tenant (`credit_balance <= 0` → silêncio total) **não dispara** quando `customer_funded` é `True` — o turno roda mesmo com o estoque do tenant zerado, porque esse crédito específico não sai mais dali.
- **`/pagamento-confirmado`**: página pública e estática do `web` (sem sessão, sem polling) — destino do `success_url`/`cancel_url` do checkout do cliente final; a confirmação de fato chega pelo WhatsApp via o webhook acima.

#### Billing gate determinístico — ✅ implementado (único mecanismo — sem rollout gradual, sem mecanismo antigo)

> `docs/superpowers/specs/2026-07-22-billing-gate-deterministico-design.md` / `docs/superpowers/plans/2026-07-22-billing-gate-implementacao.md` — desenho original (rollout gradual). `docs/superpowers/specs/2026-07-23-gate-unico-deterministico-design.md` / `docs/superpowers/plans/2026-07-23-gate-unico-deterministico.md` — remoção do mecanismo antigo, que tornou este o único caminho.

Sempre que `tenant_billing_settings.enabled = true`, o funil "sem saldo → escolher pacote → pagar → liberado" nunca passa pelo `agents` (LLM) — é uma máquina de estados determinística, inteiramente no `worker` (`apps/worker/app/billing_gate.py`), usando mensagens nativas do WhatsApp (`interactive`/`list`), zero custo de LLM. `conversations` tem o terceiro estado `billing_gate` + `billing_gate_step`/`billing_gate_retries`/`billing_gate_checkout_url` (ver Modelo de Dados). Não existe mais um mecanismo alternativo — o antigo (embutido no grafo do `agents`) foi removido de vez.

- **Entrada** (`maybe_enter_gate`, checado a cada mensagem em `process_inbound_message`, antes do fluxo normal): cobrança habilitada + não isento + saldo do contato `<= 0` → transiciona `agent`→`billing_gate`.
- **Steps**: `null` (abre o gate — manda texto de boas-vindas + lista interativa de pacotes) → `aguardando_selecao_pacote` (resolve a seleção **pelo título/nome do pacote**, já que o parser do webhook (`extract_inbound_messages`) persiste a resposta de uma lista como `title`, não `id`, e `messages.content` não guarda o `message_type` original; gera o link de pagamento via `POST /internal/end-customer-billing/checkout` e armazena em `billing_gate_checkout_url`) → `aguardando_pagamento` (reenvia o link já armazenado — nunca gera um novo).
- **Retries**: reseta a 0 a cada mudança de step, incrementa só numa resposta não reconhecida dentro do mesmo step; em `MAX_RETRIES = 3` sem sucesso, escala pra `state = "human"`.
- **Falha de envio (WhatsApp/Stripe) dentro do gate**: escala pra `human` também (não só resposta não reconhecida) — sem isso, uma falha de rede deixaria a conversa travada em `billing_gate` pra sempre, já que a válvula de escape por retry só cobria respostas, não exceções.
- **Fechamento do ciclo**: o webhook Stripe do tenant (`process_end_customer_checkout_completed` → `_send_purchase_confirmation`, `apps/api/app/services/end_customer_billing.py`) sempre transiciona a conversa direto de `billing_gate` pra `agent` (sem acionar o `agents`).
- **Fora de escopo**: `apps/web` não tem UI dedicada pro estado `billing_gate` (uma conversa nesse estado aparece como "não humana" no painel).
- ✅ **Auto-recuperação de uma corrida rara com a isenção de cobrança** (ver subseção abaixo): se o tenant isenta um contato no instante exato entre o `worker` ler o contexto (`_load_context`) e comitar a entrada no gate, a conversa pode ficar `state="billing_gate"` com `end_customer_billing_exempt=true` ao mesmo tempo. `maybe_enter_gate` detecta essa combinação na mensagem seguinte (o próprio topo da função, antes de qualquer outra checagem) e sai do gate na hora (`state="agent"`, reset de step/retries) em vez de perpetuar o bloqueio — sem isso, o curto-circuito de reentrada (`state == "billing_gate"` → `return True`) nunca chegaria a checar a isenção.
```

- [ ] **Step 3: `CLAUDE.md` — seção "Isenção de cobrança por contato"**

Troque:

```markdown
- **Quem paga**: o TENANT, sempre, enquanto isento — mesma regra já aplicada hoje pra qualquer tenant sem a cobrança do cliente final habilitada (nunca é "ninguém paga"). No `worker`, `customer_funded` é forçado `false` mesmo com saldo positivo do cliente final, e o payload `end_customer_billing` não é enviado ao `agents` — pro grafo, é como se a cobrança estivesse desligada pra esse turno, então o gate antigo (embutido no grafo, tenants em `block_with_message`) também não bloqueia nada.
```

por:

```markdown
- **Quem paga**: o TENANT, sempre, enquanto isento — mesma regra já aplicada hoje pra qualquer tenant sem a cobrança do cliente final habilitada (nunca é "ninguém paga"). No `worker`, `customer_funded` é forçado `false` mesmo com saldo positivo do cliente final.
```

- [ ] **Step 4: `apps/agents/API_AGENTS.md`**

Troque:

```markdown
    { "name": "buscar_base_conhecimento_agente", "description": "..." },
    { "name": "bucar_base_conhecimento_usuario", "description": "..." },
    { "name": "gerar_link_pagamento_cliente", "description": "..." },
    { "name": "transfer_to_agent", "description": "..." }
```

por:

```markdown
    { "name": "buscar_base_conhecimento_agente", "description": "..." },
    { "name": "bucar_base_conhecimento_usuario", "description": "..." },
    { "name": "transfer_to_agent", "description": "..." }
```

Troque:

```markdown
class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]  # histórico acumulado
    attachments: list
    conversation_id: str
    num_before_messages: int                             # janela de histórico
    current_agent_id: str | None                         # agente fixado (id na lista `agents`)
    receptive_message_specialist: bool                   # flag "primeira resposta"
    end_customer_billing: dict | None                    # saldo/pacotes do cliente final
    agents: list[dict]                                   # agentes do tenant (id, name, instructions, is_entry_point, knowledge_base_file_ids)
```

por:

```markdown
class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]  # histórico acumulado
    attachments: list
    conversation_id: str
    num_before_messages: int                             # janela de histórico
    current_agent_id: str | None                         # agente fixado (id na lista `agents`)
    receptive_message_specialist: bool                   # flag "primeira resposta"
    agents: list[dict]                                   # agentes do tenant (id, name, instructions, is_entry_point, knowledge_base_file_ids)
```

Troque:

```markdown
`agent_node`, por execução:
1. Resolve o agente ativo (`current`) e verifica o bloqueio de saldo esgotado
   do cliente final (`is_billing_blocked`) — quando bloqueado e o agente atual
   não é o ponto de entrada, `current` passa a ser o ponto de entrada (mesmo
   turno, sem um hop extra no grafo).
2. Recorta o histórico com `strip_messages(state["messages"], num_before_messages)`.
3. Monta a lista de tools (`transfer_to_agent`, `buscar_base_conhecimento_agente`,
   `bucar_base_conhecimento_usuario`, + `gerar_link_pagamento_cliente` só
   quando a cobrança do cliente final está habilitada) e faz `bind_tools`+`ainvoke`.
4. Se o modelo chamou tools → `Command(goto="tool_node")`; senão →
   `Command(goto=END)`. Em ambos os casos, `current_agent_id` é sempre
   gravado no `update`.
```

por:

```markdown
`agent_node`, por execução:
1. Resolve o agente ativo (`current`, fallback pro ponto de entrada).
2. Recorta o histórico com `strip_messages(state["messages"], num_before_messages)`.
3. Monta a lista de tools (`transfer_to_agent`, `buscar_base_conhecimento_agente`,
   `bucar_base_conhecimento_usuario`) e faz `bind_tools`+`ainvoke`.
4. Se o modelo chamou tools → `Command(goto="tool_node")`; senão →
   `Command(goto=END)`. Em ambos os casos, `current_agent_id` é sempre
   gravado no `update`.
```

Troque:

```markdown
**Despedida de transferência:** quando o modelo chama `transfer_to_agent` sem
texto próprio (e a transferência não está bloqueada por saldo), injeta-se uma
`AIMessage` do tipo _"um momento... vou te passar pra(o) X agora"_ (`X` = nome
do agente de destino) antes de ir pro `tool_node` — agora se aplica a
**qualquer** agente, não só à secretária/condominial de antes (fechamento de
um débito técnico documentado anteriormente).
```

por:

```markdown
**Despedida de transferência:** quando o modelo chama `transfer_to_agent` sem
texto próprio, injeta-se uma `AIMessage` do tipo _"um momento... vou te
passar pra(o) X agora"_ (`X` = nome do agente de destino) antes de ir pro
`tool_node` — se aplica a **qualquer** agente.
```

Troque:

```markdown
- **Injeção de `conversation_id` do estado (segurança multi-tenant):** para as
  tools em `STATE_SCOPED_TOOLS` (`bucar_base_conhecimento_usuario`,
  `buscar_base_conhecimento_agente`, `gerar_link_pagamento_cliente`), o
  `conversation_id` recebido do LLM em `tool_call["args"]` é **sempre
  sobrescrito** por `state["conversation_id"]` antes do `ainvoke`.
- **Injeção de `knowledge_base_file_ids` do agente ativo:** para
  `buscar_base_conhecimento_agente`, resolve o agente ativo
  (`state["current_agent_id"]`) em `state["agents"]` e sobrescreve
  `knowledge_base_file_ids` com os arquivos de KB anexados a ele — o LLM nunca
  escolhe quais arquivos são consultados.
- **Injeção de `valid_agent_ids`:** para `transfer_to_agent`, sobrescreve
  `valid_agent_ids` com os ids reais de `state["agents"]` — a tool recusa a
  transferência se o `agent_id` escolhido pelo LLM não estiver nessa lista.
- **Injeção de saldo do cliente final:** para `transfer_to_agent`
  (`BILLING_GATED_TOOLS`), sobrescreve `end_customer_billing_enabled`/
  `end_customer_balance` a partir de `state["end_customer_billing"]`.
- O LLM nunca decide de fato nenhum desses valores — evita que uma mensagem
  maliciosa induza a tool a vazar dado de outro tenant/agente ou a burlar o
  bloqueio de saldo.
```

por:

```markdown
- **Injeção de `conversation_id` do estado (segurança multi-tenant):** para as
  tools em `STATE_SCOPED_TOOLS` (`bucar_base_conhecimento_usuario`,
  `buscar_base_conhecimento_agente`), o `conversation_id` recebido do LLM em
  `tool_call["args"]` é **sempre sobrescrito** por `state["conversation_id"]`
  antes do `ainvoke`.
- **Injeção de `knowledge_base_file_ids` do agente ativo:** para
  `buscar_base_conhecimento_agente`, resolve o agente ativo
  (`state["current_agent_id"]`) em `state["agents"]` e sobrescreve
  `knowledge_base_file_ids` com os arquivos de KB anexados a ele — o LLM nunca
  escolhe quais arquivos são consultados.
- **Injeção de `valid_agent_ids`:** para `transfer_to_agent`, sobrescreve
  `valid_agent_ids` com os ids reais de `state["agents"]` — a tool recusa a
  transferência se o `agent_id` escolhido pelo LLM não estiver nessa lista.
- O LLM nunca decide de fato nenhum desses valores — evita que uma mensagem
  maliciosa induza a tool a vazar dado de outro tenant/agente.
```

Troque:

```markdown
| Tool                                                          | Tipo   | Função                                                                 |
|----------------------------------------------------------------|--------|------------------------------------------------------------------------|
| `transfer_to_agent(agent_id, valid_agent_ids, ...)`             | sync   | Retorna `Command` que seta `current_agent_id` e `receptive_message_specialist=True` — só se `agent_id` estiver em `valid_agent_ids` (injetado pelo `tool_node`) e o saldo do cliente final não estiver bloqueado. |
| `buscar_base_conhecimento_agente(query, conversation_id, knowledge_base_file_ids)` | async | RAG restrito aos arquivos de KB anexados ao agente ativo (injetados pelo `tool_node`), via `/retrieval/users` com `conversation_id="kb"` + `doc_ids`. |
| `bucar_base_conhecimento_usuario(query, conversation_id)`       | async  | RAG na base de documentos privados do usuário — inalterada.            |
| `gerar_link_pagamento_cliente(package_id, conversation_id)`     | async  | Gera link de pagamento (Stripe) do cliente final — inalterada.         |
| `enviar_documento(url, conversation_id)`                        | sync   | Baixa um documento de uma URL e faz upload para endpoint de inserção.  |

A lista `tools` exportada (usada pelo `tool_node`) contém as 4 primeiras da
tabela (`enviar_documento` não está bindada a nenhum agente — ver §11).
```

por:

```markdown
| Tool                                                          | Tipo   | Função                                                                 |
|----------------------------------------------------------------|--------|------------------------------------------------------------------------|
| `transfer_to_agent(agent_id, valid_agent_ids)`                  | sync   | Retorna `Command` que seta `current_agent_id` e `receptive_message_specialist=True` — só se `agent_id` estiver em `valid_agent_ids` (injetado pelo `tool_node`). |
| `buscar_base_conhecimento_agente(query, conversation_id, knowledge_base_file_ids)` | async | RAG restrito aos arquivos de KB anexados ao agente ativo (injetados pelo `tool_node`), via `/retrieval/users` com `conversation_id="kb"` + `doc_ids`. |
| `bucar_base_conhecimento_usuario(query, conversation_id)`       | async  | RAG na base de documentos privados do usuário — inalterada.            |
| `enviar_documento(url, conversation_id)`                        | sync   | Baixa um documento de uma URL e faz upload para endpoint de inserção.  |

A lista `tools` exportada (usada pelo `tool_node`) contém as 3 primeiras da
tabela (`enviar_documento` não está bindada a nenhum agente — ver §11).
```

Troque:

```markdown
Para `bucar_base_conhecimento_usuario`, `buscar_base_conhecimento_agente` e
`transfer_to_agent`, os campos que o LLM "preenche" na chamada
(`conversation_id`, `knowledge_base_file_ids`, `valid_agent_ids`,
`end_customer_billing_enabled`, `end_customer_balance`) existem na assinatura
só para permitir a injeção — o `tool_node` **sempre** sobrescreve esses
valores a partir do estado real antes de invocar (ver §5.4). Isso é o que
garante isolamento de tenant/agente: o LLM nunca controla de fato qual
tenant/conversa/base de conhecimento é consultada, nem qual agente é um
destino válido de transferência.
```

por:

```markdown
Para `bucar_base_conhecimento_usuario`, `buscar_base_conhecimento_agente` e
`transfer_to_agent`, os campos que o LLM "preenche" na chamada
(`conversation_id`, `knowledge_base_file_ids`, `valid_agent_ids`) existem na
assinatura só para permitir a injeção — o `tool_node` **sempre** sobrescreve
esses valores a partir do estado real antes de invocar (ver §5.4). Isso é o
que garante isolamento de tenant/agente: o LLM nunca controla de fato qual
tenant/conversa/base de conhecimento é consultada, nem qual agente é um
destino válido de transferência.
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md apps/agents/API_AGENTS.md
git commit -m "docs: remove o mecanismo antigo de billing da documentação — gate único determinístico"
```

---

## Residual explicitamente fora de escopo

- **`apps/web`**: nenhuma mudança — confirmado explicitamente pelo usuário.
- **Migração de dados**: não há — a própria migration que dropa a coluna já cobre todo tenant existente, automaticamente.
