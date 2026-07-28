# Isenção de cobrança por cliente final — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Botão "Cobrança gratuita" em `/conversas` (conversas reais de WhatsApp) que isenta um contato específico da cobrança do cliente final — o tenant absorve o custo enquanto isento, e nem o billing gate determinístico nem o gate antigo (embutido no `agents`) são acionados pro contato.

**Architecture:** Coluna nova `conversations.end_customer_billing_exempt` (não é tabela nova). Endpoint síncrono novo em `apps/api` que liga/desliga a flag, cancela o billing gate em andamento e avisa o contato via WhatsApp (best-effort). `apps/worker` lê a flag a cada mensagem entrante e força o turno a ser custeado pelo tenant, sem nunca montar o payload `end_customer_billing` pro `agents` nem entrar no gate determinístico enquanto isento.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (`apps/api`), Arq + SQLAlchemy Core (`apps/worker`), Next.js 15 (`apps/web`).

## Global Constraints

- **Só conversas reais de WhatsApp** — `TestConversationThread.tsx`/`POST /test-messages` não são tocados por esta feature (decisão explícita do usuário). A coluna nova existe no schema com default `false`, mas conversas de teste nunca a setam.
- **Quem paga durante a isenção**: o TENANT, sempre — mesma regra que já vale hoje pra qualquer tenant sem cobrança do cliente final habilitada. Nunca é "ninguém paga".
- **Idempotência**: chamar o endpoint de toggle com o valor que a conversa já tem não reenvia mensagem nem produz nenhum efeito colateral — só devolve o estado atual.
- **Gate em andamento**: ligar a isenção enquanto a conversa está em `billing_gate` cancela o gate na hora (`state="agent"`, `billing_gate_step=None`, `billing_gate_retries=0`) — um link de pagamento já gerado fica órfão, sem problema.
- **Aviso ao cliente é best-effort**: falha ao enviar (sem número conectado, erro da Graph API) só loga um warning — nunca desfaz a mudança de isenção nem retorna erro pro tenant.
- **Textos exatos** (usados nos dois lados — envio e teste):
  - Ligar: `"A partir de agora, essa conversa é gratuita — você não será cobrado pelo atendimento."`
  - Desligar: `"A cobrança normal foi retomada — a partir de agora, o atendimento volta a consumir seus créditos normalmente."`

---

### Task 1: Migration — `conversations.end_customer_billing_exempt`

**Files:**
- Create: `apps/api/alembic/versions/0019_isencao_cobranca_cliente_final.py`

**Interfaces:**
- Consumes: nada.
- Produces: coluna `conversations.end_customer_billing_exempt` — consumida pela Task 2 (model) e pela Task 5 (espelho no worker).

- [ ] **Step 1: Criar a migration**

Crie `apps/api/alembic/versions/0019_isencao_cobranca_cliente_final.py`:

```python
"""isenção de cobrança por cliente final — flag por conversa

Adiciona conversations.end_customer_billing_exempt: quando true, o turno é
sempre custeado pelo TENANT (nunca pelo saldo do cliente final), e nem o
billing gate determinístico (apps/worker/app/billing_gate.py) nem o gate
antigo embutido no agents são acionados pro contato — ver
docs/superpowers/specs/2026-07-23-isencao-cobranca-cliente-final-design.md.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-23
"""

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "end_customer_billing_exempt",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "end_customer_billing_exempt")
```

- [ ] **Step 2: Verificar a migration**

Se houver um Postgres real disponível (`docker compose ps postgres`): `cd apps/api && uv run alembic upgrade head`, confirme via `psql` que `conversations.end_customer_billing_exempt` existe com default `false`, depois `uv run alembic downgrade -1 && uv run alembic upgrade head` pra confirmar que sobe/desce/sobe limpo. Sem Postgres disponível, valide a sintaxe: `python3 -c "import ast; ast.parse(open('apps/api/alembic/versions/0019_isencao_cobranca_cliente_final.py').read())"`.

- [ ] **Step 3: Commit**

```bash
git add apps/api/alembic/versions/0019_isencao_cobranca_cliente_final.py
git commit -m "feat(api): migration da isenção de cobrança por cliente final"
```

---

### Task 2: Model + schemas (`apps/api`)

**Files:**
- Modify: `apps/api/app/models/conversation.py`
- Modify: `apps/api/app/schemas/conversations.py`

**Interfaces:**
- Consumes: migration `0019` (Task 1).
- Produces: `Conversation.end_customer_billing_exempt`, `ConversationOut.end_customer_billing_exempt`/`end_customer_billing_enabled`, `BillingExemptionUpdate(exempt: bool)` — consumidos pelas Tasks 3-4.

- [ ] **Step 1: Atualizar o model `Conversation`**

Em `apps/api/app/models/conversation.py`, troque:

```python
    billing_gate_checkout_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
```

por:

```python
    billing_gate_checkout_url: Mapped[str | None] = mapped_column(Text)
    end_customer_billing_exempt: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
```

(`Boolean` já está importado no topo do arquivo — usado por `is_test`.)

- [ ] **Step 2: Atualizar `ConversationOut` e adicionar `BillingExemptionUpdate`**

Em `apps/api/app/schemas/conversations.py`, troque:

```python
    end_customer_balance: float | None = None
    end_customer_cycle_total: float | None = None
    end_customer_cycle_consumed: float | None = None
```

por:

```python
    end_customer_balance: float | None = None
    end_customer_cycle_total: float | None = None
    end_customer_cycle_consumed: float | None = None
    end_customer_billing_exempt: bool = False
    end_customer_billing_enabled: bool = False
```

E adicione, depois da classe `ConversationStateUpdate`:

```python
class ConversationStateUpdate(BaseModel):
    state: Literal["agent", "human"]


class BillingExemptionUpdate(BaseModel):
    exempt: bool
```

- [ ] **Step 3: Rodar a suíte + lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Expected: todos os testes existentes continuam passando (os campos novos têm default — `ConversationOut.model_validate(conversation)` já tolera atributos ausentes em objetos de teste, mesmo padrão de `end_customer_balance` hoje) e lint limpo.

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/models/conversation.py apps/api/app/schemas/conversations.py
git commit -m "feat(api): expõe a isenção de cobrança nos models/schemas"
```

---

### Task 3: `_is_end_customer_billing_enabled` + widen os 3 endpoints existentes

**Files:**
- Modify: `apps/api/app/api/v1/conversations.py`
- Test: `apps/api/tests/unit/test_conversations_routes.py`

**Interfaces:**
- Consumes: `ConversationOut.end_customer_billing_enabled` (Task 2).
- Produces: `_is_end_customer_billing_enabled(session, tenant_id) -> bool`, `_to_conversation_out(conversation, end_customer_balance, end_customer_cycle, end_customer_billing_enabled) -> ConversationOut` (assinatura ampliada) — consumidos pela Task 4.

- [ ] **Step 1: Escrever o teste que falha**

Adicione, no fim da classe `TestEndCustomerBalance` em `apps/api/tests/unit/test_conversations_routes.py`:

```python
    def test_lista_expoe_billing_enabled_do_tenant(self, client, session) -> None:
        session.execute.side_effect = [
            _execute_returning([_conversation()]),
            _balance_result([]),
            _balance_result([]),
        ]
        session.scalar.return_value = True

        response = client.get("/api/v1/conversations")

        assert response.status_code == 200
        assert response.json()[0]["end_customer_billing_enabled"] is True

    def test_lista_sem_billing_settings_expoe_enabled_false(self, client, session) -> None:
        session.execute.side_effect = [
            _execute_returning([_conversation()]),
            _balance_result([]),
            _balance_result([]),
        ]
        session.scalar.return_value = None

        response = client.get("/api/v1/conversations")

        assert response.status_code == 200
        assert response.json()[0]["end_customer_billing_enabled"] is False
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py::TestEndCustomerBalance -v`
Expected: FAIL — `test_lista_expoe_billing_enabled_do_tenant` espera `True` mas o campo ainda não existe na resposta (vem com o default `False` do schema, então a asserção `is True` falha).

- [ ] **Step 3: Implementar `_is_end_customer_billing_enabled` e ampliar `_to_conversation_out`**

Em `apps/api/app/api/v1/conversations.py`, adicione a função nova depois de `_end_customer_cycles_by_phone` (antes de `_to_conversation_out`):

```python
async def _is_end_customer_billing_enabled(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """Se a cobrança do cliente final está habilitada pro tenant — independente
    de o contato específico já ter comprado algo ou não. Usado pra decidir se
    o botão de isenção aparece no painel (end_customer_balance sozinho não
    serve: um contato isento que nunca comprou nada teria balance=None mesmo
    com a cobrança habilitada)."""
    enabled = await session.scalar(
        select(TenantBillingSettings.enabled).where(TenantBillingSettings.tenant_id == tenant_id)
    )
    return bool(enabled)
```

Troque a assinatura e o corpo de `_to_conversation_out`:

```python
def _to_conversation_out(
    conversation: Conversation,
    end_customer_balance: Decimal | None,
    end_customer_cycle: tuple[Decimal, Decimal] | None = None,
) -> ConversationOut:
    out = ConversationOut.model_validate(conversation)
    out.end_customer_balance = (
        float(end_customer_balance) if end_customer_balance is not None else None
    )
    if end_customer_cycle is not None:
        out.end_customer_cycle_total = float(end_customer_cycle[0])
        out.end_customer_cycle_consumed = float(end_customer_cycle[1])
    return out
```

por:

```python
def _to_conversation_out(
    conversation: Conversation,
    end_customer_balance: Decimal | None,
    end_customer_cycle: tuple[Decimal, Decimal] | None = None,
    end_customer_billing_enabled: bool = False,
) -> ConversationOut:
    out = ConversationOut.model_validate(conversation)
    out.end_customer_balance = (
        float(end_customer_balance) if end_customer_balance is not None else None
    )
    if end_customer_cycle is not None:
        out.end_customer_cycle_total = float(end_customer_cycle[0])
        out.end_customer_cycle_consumed = float(end_customer_cycle[1])
    out.end_customer_billing_enabled = end_customer_billing_enabled
    return out
```

- [ ] **Step 4: Ligar nos 3 call sites existentes**

Em `list_conversations`, troque:

```python
    conversations = result.scalars().all()
    phone_numbers = [c.contact_phone_number for c in conversations]
    balances = await _end_customer_balances_by_phone(session, ctx.tenant_id, phone_numbers)
    cycles = await _end_customer_cycles_by_phone(session, ctx.tenant_id, phone_numbers)
    return [
        _to_conversation_out(
            c, balances.get(c.contact_phone_number), cycles.get(c.contact_phone_number)
        )
        for c in conversations
    ]
```

por:

```python
    conversations = result.scalars().all()
    phone_numbers = [c.contact_phone_number for c in conversations]
    balances = await _end_customer_balances_by_phone(session, ctx.tenant_id, phone_numbers)
    cycles = await _end_customer_cycles_by_phone(session, ctx.tenant_id, phone_numbers)
    billing_enabled = await _is_end_customer_billing_enabled(session, ctx.tenant_id)
    return [
        _to_conversation_out(
            c,
            balances.get(c.contact_phone_number),
            cycles.get(c.contact_phone_number),
            billing_enabled,
        )
        for c in conversations
    ]
```

Em `update_state`, troque:

```python
    await session.commit()
    balances = await _end_customer_balances_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    cycles = await _end_customer_cycles_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    return _to_conversation_out(
        conversation,
        balances.get(conversation.contact_phone_number),
        cycles.get(conversation.contact_phone_number),
    )
```

por:

```python
    await session.commit()
    balances = await _end_customer_balances_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    cycles = await _end_customer_cycles_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    billing_enabled = await _is_end_customer_billing_enabled(session, ctx.tenant_id)
    return _to_conversation_out(
        conversation,
        balances.get(conversation.contact_phone_number),
        cycles.get(conversation.contact_phone_number),
        billing_enabled,
    )
```

Em `generate_summary`, troque o bloco final (idêntico ao de `update_state` acima):

```python
    await session.commit()
    balances = await _end_customer_balances_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    cycles = await _end_customer_cycles_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    return _to_conversation_out(
        conversation,
        balances.get(conversation.contact_phone_number),
        cycles.get(conversation.contact_phone_number),
    )
```

por:

```python
    await session.commit()
    balances = await _end_customer_balances_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    cycles = await _end_customer_cycles_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    billing_enabled = await _is_end_customer_billing_enabled(session, ctx.tenant_id)
    return _to_conversation_out(
        conversation,
        balances.get(conversation.contact_phone_number),
        cycles.get(conversation.contact_phone_number),
        billing_enabled,
    )
```

- [ ] **Step 5: Rodar e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py -v`
Expected: todos os testes do arquivo passam, incluindo os 2 novos. `test_lista_vazia_nao_consulta_saldo`'s `session.execute.assert_awaited_once()` continua valendo — `_is_end_customer_billing_enabled` usa `session.scalar`, não `session.execute`, então não altera essa contagem.

- [ ] **Step 6: Rodar a suíte completa + lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Expected: tudo passa, lint limpo.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/api/v1/conversations.py apps/api/tests/unit/test_conversations_routes.py
git commit -m "feat(api): expõe end_customer_billing_enabled em ConversationOut"
```

---

### Task 4: Endpoint `PATCH /conversations/{id}/billing-exemption`

**Files:**
- Modify: `apps/api/app/api/v1/conversations.py`
- Test: `apps/api/tests/unit/test_conversations_routes.py`

**Interfaces:**
- Consumes: `_is_end_customer_billing_enabled`, `_to_conversation_out` (Task 3), `BillingExemptionUpdate` (Task 2), `_get_conversation`/`send_text_message`/`WhatsAppSendError`/`decrypt_access_token`/`WhatsAppNumber` (já existentes no arquivo).
- Produces: rota `PATCH /api/v1/conversations/{conversation_id}/billing-exemption` — consumida pela Task 7 (frontend).

- [ ] **Step 1: Atualizar o helper `_conversation()` do teste**

Em `apps/api/tests/unit/test_conversations_routes.py`, troque:

```python
def _conversation(
    state: str = "agent",
    summary: str | None = None,
    summary_generated_at=None,
    human_last_seen_at=None,
    is_test: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        contact_phone_number="5511999998888",
        state=state,
        last_message_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        summary=summary,
        summary_generated_at=summary_generated_at,
        human_last_seen_at=human_last_seen_at,
        is_test=is_test,
    )
```

por:

```python
def _conversation(
    state: str = "agent",
    summary: str | None = None,
    summary_generated_at=None,
    human_last_seen_at=None,
    is_test: bool = False,
    end_customer_billing_exempt: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        contact_phone_number="5511999998888",
        state=state,
        last_message_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        summary=summary,
        summary_generated_at=summary_generated_at,
        human_last_seen_at=human_last_seen_at,
        is_test=is_test,
        end_customer_billing_exempt=end_customer_billing_exempt,
        billing_gate_step=None,
        billing_gate_retries=0,
    )
```

(`billing_gate_step`/`billing_gate_retries` entram com default porque o endpoint nesta task ESCREVE nesses campos quando cancela o gate — precisam existir no objeto pra serem sobrescritos, mesmo que nenhum teste leia o valor inicial.)

- [ ] **Step 2: Escrever os testes que falham**

Adicione, no fim do arquivo:

```python
class TestBillingExemption:
    def test_billing_desabilitado_retorna_409(self, client, session) -> None:
        session.scalar.side_effect = [_conversation(), False]

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}/billing-exemption",
            json={"exempt": True},
        )

        assert response.status_code == 409

    def test_conversa_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}/billing-exemption",
            json={"exempt": True},
        )

        assert response.status_code == 404

    def test_ligar_isencao_cancela_gate_em_andamento_e_avisa_cliente(
        self, client, session, whatsapp_send
    ) -> None:
        conversation = _conversation(state="billing_gate")
        conversation.billing_gate_step = "aguardando_pagamento"
        conversation.billing_gate_retries = 2
        session.scalar.side_effect = [conversation, True, _number()]

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}/billing-exemption",
            json={"exempt": True},
        )

        assert response.status_code == 200
        assert conversation.end_customer_billing_exempt is True
        assert conversation.state == "agent"
        assert conversation.billing_gate_step is None
        assert conversation.billing_gate_retries == 0
        assert response.json()["end_customer_billing_exempt"] is True
        whatsapp_send.assert_awaited_once()
        assert "gratuita" in whatsapp_send.await_args.kwargs["text"]
        session.commit.assert_awaited_once()

    def test_ligar_isencao_sem_gate_ativo_nao_toca_no_state(
        self, client, session, whatsapp_send
    ) -> None:
        conversation = _conversation(state="agent")
        session.scalar.side_effect = [conversation, True, _number()]

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}/billing-exemption",
            json={"exempt": True},
        )

        assert response.status_code == 200
        assert conversation.state == "agent"

    def test_desligar_isencao_avisa_cobranca_normal(
        self, client, session, whatsapp_send
    ) -> None:
        conversation = _conversation(state="agent", end_customer_billing_exempt=True)
        session.scalar.side_effect = [conversation, True, _number()]

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}/billing-exemption",
            json={"exempt": False},
        )

        assert response.status_code == 200
        assert conversation.end_customer_billing_exempt is False
        assert "consumir seus créditos normalmente" in whatsapp_send.await_args.kwargs["text"]

    def test_valor_igual_ao_atual_e_idempotente_nao_reenvia(
        self, client, session, whatsapp_send
    ) -> None:
        conversation = _conversation(state="agent", end_customer_billing_exempt=True)
        session.scalar.side_effect = [conversation, True]

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}/billing-exemption",
            json={"exempt": True},
        )

        assert response.status_code == 200
        whatsapp_send.assert_not_awaited()
        session.commit.assert_not_awaited()

    def test_falha_ao_avisar_via_whatsapp_nao_desfaz_a_isencao(
        self, client, session, whatsapp_send
    ) -> None:
        conversation = _conversation(state="agent")
        session.scalar.side_effect = [conversation, True, _number()]
        whatsapp_send.side_effect = WhatsAppSendError("HTTP 500")

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}/billing-exemption",
            json={"exempt": True},
        )

        assert response.status_code == 200
        assert conversation.end_customer_billing_exempt is True
        session.commit.assert_awaited_once()

    def test_sem_numero_conectado_nao_desfaz_a_isencao(
        self, client, session, whatsapp_send
    ) -> None:
        conversation = _conversation(state="agent")
        session.scalar.side_effect = [conversation, True, None]

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}/billing-exemption",
            json={"exempt": True},
        )

        assert response.status_code == 200
        assert conversation.end_customer_billing_exempt is True
        whatsapp_send.assert_not_awaited()
```

- [ ] **Step 3: Rodar e confirmar a falha**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py::TestBillingExemption -v`
Expected: FAIL — `404 Not Found` em todos os testes (a rota ainda não existe).

- [ ] **Step 4: Implementar a rota**

Em `apps/api/app/api/v1/conversations.py`, adicione, depois de `update_state` (antes de `heartbeat`):

```python
@router.patch("/{conversation_id}/billing-exemption")
async def update_billing_exemption(
    conversation_id: uuid.UUID,
    body: BillingExemptionUpdate,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ConversationOut:
    """Isenta (ou reativa a cobrança de) um contato da cobrança do cliente
    final — o tenant absorve o custo enquanto isento (mesma regra já
    aplicada hoje pra qualquer tenant sem a cobrança habilitada). Cancela o
    billing gate em andamento, se houver, ao isentar."""
    conversation = await _get_conversation(conversation_id, ctx, session)

    billing_enabled = await _is_end_customer_billing_enabled(session, ctx.tenant_id)
    if not billing_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cobrança do cliente final não habilitada — nada para isentar",
        )

    if conversation.end_customer_billing_exempt == body.exempt:
        # Idempotente: mesmo valor já vigente, não reenvia aviso nem comita nada.
        balances = await _end_customer_balances_by_phone(
            session, ctx.tenant_id, [conversation.contact_phone_number]
        )
        cycles = await _end_customer_cycles_by_phone(
            session, ctx.tenant_id, [conversation.contact_phone_number]
        )
        return _to_conversation_out(
            conversation,
            balances.get(conversation.contact_phone_number),
            cycles.get(conversation.contact_phone_number),
            billing_enabled,
        )

    if body.exempt:
        if conversation.state == "billing_gate":
            conversation.state = "agent"
            conversation.billing_gate_step = None
            conversation.billing_gate_retries = 0
        conversation.end_customer_billing_exempt = True
        notice_text = (
            "A partir de agora, essa conversa é gratuita — você não será "
            "cobrado pelo atendimento."
        )
    else:
        conversation.end_customer_billing_exempt = False
        notice_text = (
            "A cobrança normal foi retomada — a partir de agora, o "
            "atendimento volta a consumir seus créditos normalmente."
        )

    await session.commit()

    number = await session.scalar(
        select(WhatsAppNumber).where(
            WhatsAppNumber.tenant_id == ctx.tenant_id,
            WhatsAppNumber.status == "connected",
        )
    )
    if number is None:
        logger.warning(
            "Sem número conectado pra avisar sobre mudança de isenção | conversation=%s",
            conversation_id,
        )
    else:
        try:
            await send_text_message(
                phone_number_id=number.phone_number_id,
                access_token=decrypt_access_token(number.access_token_encrypted),
                to=conversation.contact_phone_number,
                text=notice_text,
            )
        except WhatsAppSendError as exc:
            logger.warning(
                "Falha ao avisar cliente sobre mudança de isenção | conversation=%s erro=%s",
                conversation_id,
                exc,
            )

    balances = await _end_customer_balances_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    cycles = await _end_customer_cycles_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    return _to_conversation_out(
        conversation,
        balances.get(conversation.contact_phone_number),
        cycles.get(conversation.contact_phone_number),
        billing_enabled,
    )
```

E importe `BillingExemptionUpdate` no bloco de imports de `app.schemas.conversations`:

```python
from app.schemas.conversations import (
    BillingExemptionUpdate,
    ConversationOut,
    ConversationStateUpdate,
    ConversationUsageOut,
    MessageOut,
    SendMessageRequest,
)
```

- [ ] **Step 5: Rodar e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py::TestBillingExemption -v`
Expected: todos os 7 testes passam.

- [ ] **Step 6: Rodar a suíte completa + lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Expected: tudo passa, lint limpo.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/api/v1/conversations.py apps/api/tests/unit/test_conversations_routes.py
git commit -m "feat(api): endpoint de isenção de cobrança por cliente final"
```

---

### Task 5: Espelho no `apps/worker` — tables.py + InboundContext + `_load_context`

**Files:**
- Modify: `apps/worker/app/tables.py`
- Modify: `apps/worker/app/tasks/inbound_context.py`
- Modify: `apps/worker/app/tasks/messages.py`
- Test: `apps/worker/tests/unit/test_load_context.py`

**Interfaces:**
- Consumes: migration `0019` (Task 1, mesmo banco).
- Produces: `InboundContext.end_customer_billing_exempt: bool` — consumido pela Task 6.

- [ ] **Step 1: Espelhar a coluna na Core table**

Em `apps/worker/app/tables.py`, troque:

```python
conversations = Table(
    "conversations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("contact_phone_number", String),
    Column("state", String),
    Column("is_test", Boolean, nullable=False),
    Column("last_message_at", DateTime(timezone=True)),
    Column("human_last_seen_at", DateTime(timezone=True)),
    Column("billing_gate_step", String),
    Column("billing_gate_retries", Integer),
    Column("billing_gate_checkout_url", Text),
)
```

por:

```python
conversations = Table(
    "conversations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("contact_phone_number", String),
    Column("state", String),
    Column("is_test", Boolean, nullable=False),
    Column("last_message_at", DateTime(timezone=True)),
    Column("human_last_seen_at", DateTime(timezone=True)),
    Column("billing_gate_step", String),
    Column("billing_gate_retries", Integer),
    Column("billing_gate_checkout_url", Text),
    Column("end_customer_billing_exempt", Boolean, nullable=False),
)
```

- [ ] **Step 2: Atualizar o teste helper `_conversation()` e escrever o teste que falha**

Em `apps/worker/tests/unit/test_load_context.py`, troque:

```python
def _conversation(**overrides):
    row = SimpleNamespace(
        state="agent",
        contact_phone_number="5511999998888",
        human_last_seen_at=None,
        billing_gate_step=None,
        billing_gate_retries=0,
        billing_gate_checkout_url=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row
```

por:

```python
def _conversation(**overrides):
    row = SimpleNamespace(
        state="agent",
        contact_phone_number="5511999998888",
        human_last_seen_at=None,
        billing_gate_step=None,
        billing_gate_retries=0,
        billing_gate_checkout_url=None,
        end_customer_billing_exempt=False,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row
```

Adicione, no fim do arquivo:

```python
async def test_carrega_isencao_de_cobranca_da_conversa() -> None:
    session = _session_with(
        conversation=_conversation(end_customer_billing_exempt=True),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=None,
        balance=None,
        packages=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.end_customer_billing_exempt is True


async def test_isencao_default_e_false() -> None:
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

    assert context.end_customer_billing_exempt is False
```

- [ ] **Step 3: Rodar e confirmar a falha**

Run: `cd apps/worker && uv run pytest tests/unit/test_load_context.py -v`
Expected: FAIL — `AttributeError: 'InboundContext' object has no attribute 'end_customer_billing_exempt'` (o campo ainda não existe na dataclass).

- [ ] **Step 4: Adicionar o campo em `InboundContext`**

Em `apps/worker/app/tasks/inbound_context.py`, troque:

```python
    insufficient_balance_policy: str = "block_with_message"
    billing_gate_welcome_text: str | None = None
```

por:

```python
    insufficient_balance_policy: str = "block_with_message"
    billing_gate_welcome_text: str | None = None
    end_customer_billing_exempt: bool = False
```

- [ ] **Step 5: Widenar a query da conversa em `_load_context`**

Em `apps/worker/app/tasks/messages.py`, troque:

```python
    conversation = (
        await session.execute(
            select(
                tables.conversations.c.state,
                tables.conversations.c.contact_phone_number,
                tables.conversations.c.human_last_seen_at,
                tables.conversations.c.billing_gate_step,
                tables.conversations.c.billing_gate_retries,
                tables.conversations.c.billing_gate_checkout_url,
            ).where(tables.conversations.c.id == uuid.UUID(conversation_id))
        )
    ).one_or_none()
```

por:

```python
    conversation = (
        await session.execute(
            select(
                tables.conversations.c.state,
                tables.conversations.c.contact_phone_number,
                tables.conversations.c.human_last_seen_at,
                tables.conversations.c.billing_gate_step,
                tables.conversations.c.billing_gate_retries,
                tables.conversations.c.billing_gate_checkout_url,
                tables.conversations.c.end_customer_billing_exempt,
            ).where(tables.conversations.c.id == uuid.UUID(conversation_id))
        )
    ).one_or_none()
```

E no `return InboundContext(...)` final, troque:

```python
        insufficient_balance_policy=(
            billing_settings.insufficient_balance_policy
            if billing_settings is not None
            else "block_with_message"
        ),
        billing_gate_welcome_text=(
            billing_settings.billing_gate_welcome_text if billing_settings is not None else None
        ),
    )
```

por:

```python
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

- [ ] **Step 6: Rodar e confirmar sucesso**

Run: `cd apps/worker && uv run pytest tests/unit/test_load_context.py -v`
Expected: todos os testes do arquivo passam, incluindo os 2 novos. `test_sem_agentes_retorna_lista_vazia`'s `assert session.execute.await_count == 7` continua valendo — a coluna nova foi adicionada à query da conversa que já existia, nenhuma query nova foi criada.

- [ ] **Step 7: Rodar a suíte completa do worker**

Run: `cd apps/worker && uv run pytest tests/unit -q`
Expected: tudo passa.

- [ ] **Step 8: Commit**

```bash
git add apps/worker/app/tables.py apps/worker/app/tasks/inbound_context.py apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_load_context.py
git commit -m "feat(worker): carrega end_customer_billing_exempt em InboundContext"
```

---

### Task 6: Worker respeita a isenção — `maybe_enter_gate` + `process_inbound_message`

**Files:**
- Modify: `apps/worker/app/billing_gate.py`
- Modify: `apps/worker/app/tasks/messages.py`
- Test: `apps/worker/tests/unit/test_billing_gate.py`
- Test: `apps/worker/tests/unit/test_process_inbound_message.py`

**Interfaces:**
- Consumes: `InboundContext.end_customer_billing_exempt` (Task 5).
- Produces: nada consumido por outra task deste plano — última task de backend.

- [ ] **Step 1: Escrever o teste que falha — `maybe_enter_gate`**

Em `apps/worker/tests/unit/test_billing_gate.py`, adicione, no fim de `class TestMaybeEnterGate`:

```python
    async def test_nao_entra_quando_contato_esta_isento(self) -> None:
        session = AsyncMock()
        inbound = _inbound(end_customer_balance=Decimal(0), end_customer_billing_exempt=True)

        entered = await maybe_enter_gate(session, TENANT_ID, CONVERSATION_ID, inbound)

        assert entered is False
        session.execute.assert_not_called()
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd apps/worker && uv run pytest tests/unit/test_billing_gate.py::TestMaybeEnterGate::test_nao_entra_quando_contato_esta_isento -v`
Expected: FAIL — sem o guard, a condição de entrada (`saldo <= 0` + `policy == deterministic_gate` + `enabled`) ainda é satisfeita, então `entered` vem `True`.

- [ ] **Step 3: Implementar o guard em `maybe_enter_gate`**

Em `apps/worker/app/billing_gate.py`, troque:

```python
    if (
        inbound.conversation_state == "agent"
        and inbound.end_customer_billing_enabled
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
        and inbound.insufficient_balance_policy == "deterministic_gate"
        and inbound.end_customer_balance <= 0
    ):
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/worker && uv run pytest tests/unit/test_billing_gate.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 5: Escrever os testes que falham — `process_inbound_message`**

Em `apps/worker/tests/unit/test_process_inbound_message.py`, troque a assinatura de `_inbound_com_billing`:

```python
def _inbound_com_billing(balance: int, credit_balance: int = 1000) -> InboundContext:
    return InboundContext(
        conversation_state="agent",
        contact_phone_number="5511888888888",
        message_content="Olá",
        phone_number_id="PNID",
        access_token_encrypted="token-cifrado",
        credit_balance=Decimal(credit_balance),
        end_customer_billing_enabled=True,
        end_customer_balance=Decimal(balance),
        end_customer_packages=[
            {"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}
        ],
        agents=[],
    )
```

por:

```python
def _inbound_com_billing(
    balance: int, credit_balance: int = 1000, exempt: bool = False
) -> InboundContext:
    return InboundContext(
        conversation_state="agent",
        contact_phone_number="5511888888888",
        message_content="Olá",
        phone_number_id="PNID",
        access_token_encrypted="token-cifrado",
        credit_balance=Decimal(credit_balance),
        end_customer_billing_enabled=True,
        end_customer_balance=Decimal(balance),
        end_customer_packages=[
            {"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}
        ],
        agents=[],
        end_customer_billing_exempt=exempt,
    )
```

Adicione, no fim do arquivo:

```python
async def test_contato_isento_nunca_e_customer_funded(patched) -> None:
    patched["load"].return_value = _inbound_com_billing(balance=1000, exempt=True)
    patched["send"].return_value = {"responses": ["oi"], "tokens_used": 2000}

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()
    assert "end_customer_billing" not in patched["send"].await_args.kwargs


async def test_contato_isento_com_saldo_do_tenant_zerado_fica_em_silencio(patched) -> None:
    patched["load"].return_value = _inbound_com_billing(
        balance=1000, credit_balance=0, exempt=True
    )

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_not_awaited()
```

- [ ] **Step 6: Rodar e confirmar a falha**

Run: `cd apps/worker && uv run pytest tests/unit/test_process_inbound_message.py -v`
Expected: FAIL nos 2 testes novos — `test_contato_isento_nunca_e_customer_funded` porque hoje `end_customer_billing` ainda é enviado (a condição só olha `end_customer_billing_enabled`, não a isenção); `test_contato_isento_com_saldo_do_tenant_zerado_fica_em_silencio` porque hoje `customer_funded` ficaria `True` (saldo do cliente final positivo, isenção ainda não é considerada), então o silêncio por saldo zerado do tenant NÃO dispara e o agente É chamado.

- [ ] **Step 7: Implementar o guard em `process_inbound_message`**

Em `apps/worker/app/tasks/messages.py`, troque:

```python
    customer_funded = inbound.end_customer_billing_enabled and inbound.end_customer_balance > 0
```

por:

```python
    customer_funded = (
        not inbound.end_customer_billing_exempt
        and inbound.end_customer_billing_enabled
        and inbound.end_customer_balance > 0
    )
```

E troque:

```python
    extra_kwargs: dict = {}
    if inbound.end_customer_billing_enabled:
        extra_kwargs["end_customer_billing"] = {
            "enabled": True,
            "balance": inbound.end_customer_balance,
            "packages": inbound.end_customer_packages,
        }
```

por:

```python
    extra_kwargs: dict = {}
    if inbound.end_customer_billing_enabled and not inbound.end_customer_billing_exempt:
        extra_kwargs["end_customer_billing"] = {
            "enabled": True,
            "balance": inbound.end_customer_balance,
            "packages": inbound.end_customer_packages,
        }
```

- [ ] **Step 8: Rodar e confirmar sucesso**

Run: `cd apps/worker && uv run pytest tests/unit/test_process_inbound_message.py -v`
Expected: todos os testes do arquivo passam, incluindo os 2 novos.

- [ ] **Step 9: Rodar a suíte completa do worker + lint**

Run: `cd apps/worker && uv run pytest tests/unit -q && uv run ruff check app/billing_gate.py app/tasks/messages.py`
Expected: tudo passa, sem erro novo.

- [ ] **Step 10: Commit**

```bash
git add apps/worker/app/billing_gate.py apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_billing_gate.py apps/worker/tests/unit/test_process_inbound_message.py
git commit -m "feat(worker): contato isento nunca entra no billing gate nem é customer_funded"
```

---

### Task 7: Frontend — switch "Cobrança gratuita" em `ConversationThread.tsx`

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/components/ConversationThread.tsx`

**Interfaces:**
- Consumes: `PATCH /api/v1/conversations/{id}/billing-exemption` (Task 4), `Conversation.end_customer_billing_exempt`/`end_customer_billing_enabled` (Task 2, via `ConversationOut`).
- Produces: nada consumido por outra task — última task do plano.

- [ ] **Step 1: Atualizar o tipo `Conversation`**

Em `apps/web/src/lib/types.ts`, troque:

```typescript
export interface Conversation {
  id: string;
  contact_phone_number: string;
  state: ConversationState;
  is_test: boolean;
  last_message_at: string | null;
  created_at: string;
  summary: string | null;
  summary_generated_at: string | null;
  end_customer_balance?: number | null;
  end_customer_cycle_total?: number | null;
  end_customer_cycle_consumed?: number | null;
}
```

por:

```typescript
export interface Conversation {
  id: string;
  contact_phone_number: string;
  state: ConversationState;
  is_test: boolean;
  last_message_at: string | null;
  created_at: string;
  summary: string | null;
  summary_generated_at: string | null;
  end_customer_balance?: number | null;
  end_customer_cycle_total?: number | null;
  end_customer_cycle_consumed?: number | null;
  end_customer_billing_exempt: boolean;
  end_customer_billing_enabled: boolean;
}
```

- [ ] **Step 2: Adicionar o estado local e a função de toggle**

Em `apps/web/src/components/ConversationThread.tsx`, depois da declaração de `isManual` (`const isManual = conversation.state === "human";`), adicione:

```typescript
  const [exemptionError, setExemptionError] = useState<string | null>(null);

  const toggleBillingExemption = async () => {
    const goingExempt = !conversation.end_customer_billing_exempt;
    const confirmed = goingExempt
      ? window.confirm(
          "Isentar este cliente de cobrança? Ele poderá conversar livremente e receberá um aviso de que a conversa passou a ser gratuita.",
        )
      : window.confirm(
          "A partir da próxima mensagem, esse cliente volta a ser cobrado normalmente. Confirmar?",
        );
    if (!confirmed) {
      return;
    }
    setExemptionError(null);
    const response = await backendFetch(`conversations/${conversation.id}/billing-exemption`, {
      method: "PATCH",
      body: JSON.stringify({ exempt: goingExempt }),
    });
    if (response.ok) {
      onConversationUpdate(await response.json());
    } else {
      setExemptionError("Não foi possível alterar a cobrança deste cliente. Tente novamente.");
    }
  };
```

- [ ] **Step 3: Renderizar o switch e o erro**

No cabeçalho (`<header>`), troque:

```tsx
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-muted">IA respondendo</span>
            <button
              type="button"
              role="switch"
              aria-checked={!isManual}
              aria-label="IA respondendo"
              onClick={() => void toggleState()}
              className={`relative h-5 w-9 rounded-full transition-colors ${
                !isManual ? "bg-accent" : "bg-line"
              }`}
            >
              <span
                aria-hidden
                className={`absolute top-0.5 h-4 w-4 rounded-full bg-surface transition-transform ${
                  !isManual ? "translate-x-4" : "translate-x-0.5"
                }`}
              />
            </button>
          </div>
          <button
            type="button"
            onClick={() => void handleDelete()}
            className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
          >
            Excluir conversa
          </button>
        </div>
      </header>
```

por:

```tsx
        <div className="flex items-center gap-4">
          {conversation.end_customer_billing_enabled ? (
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-muted">Cobrança gratuita</span>
              <button
                type="button"
                role="switch"
                aria-checked={conversation.end_customer_billing_exempt}
                aria-label="Cobrança gratuita"
                onClick={() => void toggleBillingExemption()}
                className={`relative h-5 w-9 rounded-full transition-colors ${
                  conversation.end_customer_billing_exempt ? "bg-accent" : "bg-line"
                }`}
              >
                <span
                  aria-hidden
                  className={`absolute top-0.5 h-4 w-4 rounded-full bg-surface transition-transform ${
                    conversation.end_customer_billing_exempt
                      ? "translate-x-4"
                      : "translate-x-0.5"
                  }`}
                />
              </button>
            </div>
          ) : null}
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-muted">IA respondendo</span>
            <button
              type="button"
              role="switch"
              aria-checked={!isManual}
              aria-label="IA respondendo"
              onClick={() => void toggleState()}
              className={`relative h-5 w-9 rounded-full transition-colors ${
                !isManual ? "bg-accent" : "bg-line"
              }`}
            >
              <span
                aria-hidden
                className={`absolute top-0.5 h-4 w-4 rounded-full bg-surface transition-transform ${
                  !isManual ? "translate-x-4" : "translate-x-0.5"
                }`}
              />
            </button>
          </div>
          <button
            type="button"
            onClick={() => void handleDelete()}
            className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
          >
            Excluir conversa
          </button>
        </div>
      </header>
      {exemptionError ? (
        <p role="alert" className="border-b border-line bg-surface px-6 py-2 text-xs text-danger">
          {exemptionError}
        </p>
      ) : null}
```

- [ ] **Step 4: Testar manualmente no navegador**

Suba o `web`/`api` locais (`docker compose up -d web api` ou equivalente já usado no projeto). Com um tenant que tem `tenant_billing_settings.enabled = true` (ex: via `/configuracoes/cobranca-clientes`), abra `/conversas`, selecione uma conversa real e confirme:
- O switch "Cobrança gratuita" aparece só quando a cobrança do cliente final está habilitada.
- Ligar pede confirmação, e depois de confirmar o switch fica ativo.
- Desligar pede a confirmação de aviso de cobrança normal.
- Numa conversa sem a cobrança habilitada pro tenant, o switch não aparece.

- [ ] **Step 5: Rodar lint/build do web**

Run: `cd apps/web && pnpm lint && pnpm build`
Expected: sem erros de tipo (o `Conversation` agora exige os 2 campos novos — confirme que nenhum outro lugar do código constrói um objeto `Conversation` sem eles; se algum teste/mock do `apps/web` construir um `Conversation` manualmente, adicione os 2 campos lá também).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/components/ConversationThread.tsx
git commit -m "feat(web): switch de isenção de cobrança em /conversas"
```

---

## Residual explicitamente fora de escopo

- **Conversas de teste**: `TestConversationThread.tsx`/`POST /test-messages` não ganham esse botão nem consideram a flag — decisão explícita do usuário nesta sessão.
- **Histórico de quem ligou/desligou a isenção**: sem auditoria dedicada, sem mensagem persistida em `messages` pro aviso — só o WhatsApp direto e o estado atual da flag.
- **Expiração automática da isenção**: dura até ser desligada manualmente.
