# Consolidação de billing do cliente final + indicador de ciclo por conversa Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidar as 2 peças de "cobrança do cliente final" que hoje vivem em lugares diferentes (`/configuracoes/cobranca-clientes` e a aba "Consumo" de `/conversas`) em 3 abas de uma página só; adicionar, em cada conversa, um indicador de créditos comprados/consumidos no ciclo atual (reseta a cada nova compra).

**Architecture:** Backend ganha uma função de cálculo derivada 100% do ledger existente (sem migração) + 2 campos novos em `ConversationOut`. Frontend ganha um componente wrapper novo (`EndCustomerBillingTabs.tsx`) que reúne 3 componentes já existentes como abas, e 2 componentes já existentes (`ConversationList`/`ConversationThread`) ganham um span adicional.

**Tech Stack:** FastAPI + SQLAlchemy async (`apps/api`), Next.js/React (`apps/web`). Testes: `pytest` (`apps/api`), Vitest (`apps/web`).

## Global Constraints

- Sem migração de banco — o ciclo é derivado do ledger `end_customer_credit_transactions` em tempo de leitura.
- "Total" do ciclo = `amount_credits` da transação `type="purchase"` mais recente do contato; "consumido" = soma (valor absoluto) de `type="consumption"` com `created_at` posterior à dessa compra. Contato sem nenhuma compra não tem ciclo (campos ficam `null`, igual ao padrão já usado por `end_customer_balance`).
- O indicador de ciclo **complementa** o saldo (`end_customer_balance`) já exibido — nunca o substitui.
- `/creditos` (wallet do tenant com a plataforma) não é tocado por este plano.
- Mesmo padrão de abas já usado em `ConversationsPanel.tsx` (`useState` local + botões com `aria-pressed`) — replicar, não inventar um padrão novo.

---

### Task 1: Backend — cálculo do ciclo de créditos por contato

**Files:**
- Modify: `apps/api/app/schemas/conversations.py`
- Modify: `apps/api/app/api/v1/conversations.py`
- Test: `apps/api/tests/unit/test_conversations_routes.py`

**Interfaces:**
- Consumes: nada de fora deste arquivo.
- Produces: `ConversationOut.end_customer_cycle_total: float | None`, `ConversationOut.end_customer_cycle_consumed: float | None` — consumidos pela Task 2 (frontend).

- [ ] **Step 1: Adicionar os campos ao schema**

Em `apps/api/app/schemas/conversations.py`, troque:

```python
    end_customer_balance: float | None = None
```

por:

```python
    end_customer_balance: float | None = None
    end_customer_cycle_total: float | None = None
    end_customer_cycle_consumed: float | None = None
```

- [ ] **Step 2: Escrever os testes de `_end_customer_cycles_by_phone` (função isolada)**

Em `apps/api/tests/unit/test_conversations_routes.py`, adicione, no final do arquivo:

```python
class TestEndCustomerCycleCalculation:
    async def test_sem_nenhuma_compra_retorna_vazio(self) -> None:
        from app.api.v1.conversations import _end_customer_cycles_by_phone

        session = AsyncMock()
        session.execute.return_value = _balance_result([])

        result = await _end_customer_cycles_by_phone(session, TENANT_ID, ["5511999998888"])

        assert result == {}
        session.execute.assert_awaited_once()

    async def test_uma_compra_sem_consumo(self) -> None:
        from app.api.v1.conversations import _end_customer_cycles_by_phone

        session = AsyncMock()
        purchased_at = datetime.now(UTC)
        session.execute.side_effect = [
            _balance_result(
                [
                    SimpleNamespace(
                        contact_phone_number="5511999998888",
                        amount_credits=Decimal("200"),
                        created_at=purchased_at,
                    )
                ]
            ),
            _balance_result([]),
        ]

        result = await _end_customer_cycles_by_phone(session, TENANT_ID, ["5511999998888"])

        assert result == {"5511999998888": (Decimal("200"), Decimal("0"))}

    async def test_uma_compra_com_consumo_parcial(self) -> None:
        from app.api.v1.conversations import _end_customer_cycles_by_phone

        session = AsyncMock()
        purchased_at = datetime(2026, 7, 1, tzinfo=UTC)
        session.execute.side_effect = [
            _balance_result(
                [
                    SimpleNamespace(
                        contact_phone_number="5511999998888",
                        amount_credits=Decimal("200"),
                        created_at=purchased_at,
                    )
                ]
            ),
            _balance_result(
                [
                    SimpleNamespace(
                        contact_phone_number="5511999998888",
                        amount_credits=Decimal("-30"),
                        created_at=datetime(2026, 7, 5, tzinfo=UTC),
                    )
                ]
            ),
        ]

        result = await _end_customer_cycles_by_phone(session, TENANT_ID, ["5511999998888"])

        assert result == {"5511999998888": (Decimal("200"), Decimal("30"))}

    async def test_duas_compras_so_conta_consumo_depois_da_mais_recente(self) -> None:
        from app.api.v1.conversations import _end_customer_cycles_by_phone

        session = AsyncMock()
        first_purchase = datetime(2026, 6, 1, tzinfo=UTC)
        second_purchase = datetime(2026, 7, 1, tzinfo=UTC)
        session.execute.side_effect = [
            _balance_result(
                [
                    SimpleNamespace(
                        contact_phone_number="5511999998888",
                        amount_credits=Decimal("100"),
                        created_at=first_purchase,
                    ),
                    SimpleNamespace(
                        contact_phone_number="5511999998888",
                        amount_credits=Decimal("200"),
                        created_at=second_purchase,
                    ),
                ]
            ),
            _balance_result(
                [
                    # consumo ANTES da 2ª compra — não deve contar
                    SimpleNamespace(
                        contact_phone_number="5511999998888",
                        amount_credits=Decimal("-50"),
                        created_at=datetime(2026, 6, 15, tzinfo=UTC),
                    ),
                    # consumo DEPOIS da 2ª compra — deve contar
                    SimpleNamespace(
                        contact_phone_number="5511999998888",
                        amount_credits=Decimal("-20"),
                        created_at=datetime(2026, 7, 10, tzinfo=UTC),
                    ),
                ]
            ),
        ]

        result = await _end_customer_cycles_by_phone(session, TENANT_ID, ["5511999998888"])

        assert result == {"5511999998888": (Decimal("200"), Decimal("20"))}
```

- [ ] **Step 3: Rodar e confirmar a falha**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py -v -k TestEndCustomerCycleCalculation`
Expected: FAIL — `ImportError: cannot import name '_end_customer_cycles_by_phone'`.

- [ ] **Step 4: Implementar `_end_customer_cycles_by_phone`**

Em `apps/api/app/api/v1/conversations.py`, adicione a função logo depois de `_end_customer_balances_by_phone` (antes de `_to_conversation_out`):

```python
async def _end_customer_cycles_by_phone(
    session: AsyncSession, tenant_id: uuid.UUID, phone_numbers: list[str]
) -> dict[str, tuple[Decimal, Decimal]]:
    """Ciclo de créditos atual por contato: total da compra mais recente e
    quanto já foi consumido desde ela — reseta a cada nova compra (não é o
    total/consumo vitalício, que fica na aba "Clientes" de
    /configuracoes/cobranca-clientes). Só populado quando a cobrança do
    cliente final está habilitada pro tenant (mesmo gate de
    _end_customer_balances_by_phone); contato sem nenhuma compra não
    aparece no dict retornado."""
    if not phone_numbers:
        return {}

    purchases_result = await session.execute(
        select(
            EndCustomerCreditTransaction.contact_phone_number,
            EndCustomerCreditTransaction.amount_credits,
            EndCustomerCreditTransaction.created_at,
        )
        .join(
            TenantBillingSettings,
            TenantBillingSettings.tenant_id == EndCustomerCreditTransaction.tenant_id,
        )
        .where(
            TenantBillingSettings.enabled.is_(True),
            EndCustomerCreditTransaction.tenant_id == tenant_id,
            EndCustomerCreditTransaction.contact_phone_number.in_(phone_numbers),
            EndCustomerCreditTransaction.type == "purchase",
        )
    )
    latest_purchase: dict[str, tuple[Decimal, datetime]] = {}
    for row in purchases_result.all():
        current = latest_purchase.get(row.contact_phone_number)
        if current is None or row.created_at > current[1]:
            latest_purchase[row.contact_phone_number] = (row.amount_credits, row.created_at)

    if not latest_purchase:
        return {}

    consumption_result = await session.execute(
        select(
            EndCustomerCreditTransaction.contact_phone_number,
            EndCustomerCreditTransaction.amount_credits,
            EndCustomerCreditTransaction.created_at,
        ).where(
            EndCustomerCreditTransaction.tenant_id == tenant_id,
            EndCustomerCreditTransaction.contact_phone_number.in_(latest_purchase.keys()),
            EndCustomerCreditTransaction.type == "consumption",
        )
    )
    consumption_rows = consumption_result.all()

    cycles: dict[str, tuple[Decimal, Decimal]] = {}
    for phone, (total, purchased_at) in latest_purchase.items():
        consumed = sum(
            (
                -row.amount_credits
                for row in consumption_rows
                if row.contact_phone_number == phone and row.created_at > purchased_at
            ),
            start=Decimal(0),
        )
        cycles[phone] = (total, consumed)
    return cycles
```

- [ ] **Step 5: Rodar e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py -v -k TestEndCustomerCycleCalculation`
Expected: os 4 testes passam.

- [ ] **Step 6: Ligar o cálculo do ciclo em `_to_conversation_out`**

Em `apps/api/app/api/v1/conversations.py`, troque:

```python
def _to_conversation_out(
    conversation: Conversation, end_customer_balance: Decimal | None
) -> ConversationOut:
    out = ConversationOut.model_validate(conversation)
    out.end_customer_balance = (
        float(end_customer_balance) if end_customer_balance is not None else None
    )
    return out
```

por:

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

- [ ] **Step 7: Chamar `_end_customer_cycles_by_phone` nos 3 pontos que já chamam `_end_customer_balances_by_phone`**

Em `list_conversations`, troque:

```python
    conversations = result.scalars().all()
    balances = await _end_customer_balances_by_phone(
        session, ctx.tenant_id, [c.contact_phone_number for c in conversations]
    )
    return [
        _to_conversation_out(c, balances.get(c.contact_phone_number)) for c in conversations
    ]
```

por:

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

Em `update_state`, troque:

```python
    if body.state == "human":
        # Takeover começa "presente" — o heartbeat do painel mantém depois.
        conversation.human_last_seen_at = datetime.now(UTC)
    await session.commit()
    balances = await _end_customer_balances_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    return _to_conversation_out(conversation, balances.get(conversation.contact_phone_number))
```

por:

```python
    if body.state == "human":
        # Takeover começa "presente" — o heartbeat do painel mantém depois.
        conversation.human_last_seen_at = datetime.now(UTC)
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

Em `generate_summary`, localize o bloco final (depois do `update(Tenant)...`) e troque:

```python
    await session.commit()
    balances = await _end_customer_balances_by_phone(
        session, ctx.tenant_id, [conversation.contact_phone_number]
    )
    return _to_conversation_out(conversation, balances.get(conversation.contact_phone_number))
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
    return _to_conversation_out(
        conversation,
        balances.get(conversation.contact_phone_number),
        cycles.get(conversation.contact_phone_number),
    )
```

(Este bloco de 4 linhas é idêntico nos dois lugares — `update_state` e `generate_summary` — mas o contexto imediatamente anterior de cada um é diferente, use isso para achar o ponto certo em cada função.)

- [ ] **Step 8: Atualizar os testes de `TestEndCustomerBalance` (3 chamadas de `session.execute` em vez de 2)**

Em `apps/api/tests/unit/test_conversations_routes.py`, cada teste abaixo precisa de mais 1 item no `side_effect` (a nova consulta de "última compra" — vazia, já que nenhum desses testes mocka uma compra):

`test_lista_inclui_saldo_do_cliente_final_quando_ha_registro`:

```python
    def test_lista_inclui_saldo_do_cliente_final_quando_ha_registro(
        self, client, session
    ) -> None:
        session.execute.side_effect = [
            _execute_returning([_conversation()]),
            _balance_result(
                [
                    SimpleNamespace(
                        contact_phone_number="5511999998888", credit_balance=Decimal("42")
                    )
                ]
            ),
            _balance_result([]),
        ]

        response = client.get("/api/v1/conversations")

        assert response.status_code == 200
        assert response.json()[0]["end_customer_balance"] == 42.0
        assert response.json()[0]["end_customer_cycle_total"] is None
```

`test_lista_sem_saldo_encontrado_retorna_null`:

```python
    def test_lista_sem_saldo_encontrado_retorna_null(self, client, session) -> None:
        session.execute.side_effect = [
            _execute_returning([_conversation()]),
            _balance_result([]),
            _balance_result([]),
        ]

        response = client.get("/api/v1/conversations")

        assert response.status_code == 200
        assert response.json()[0]["end_customer_balance"] is None
```

`test_takeover_devolve_saldo_do_cliente_final`:

```python
    def test_takeover_devolve_saldo_do_cliente_final(self, client, session) -> None:
        conversation = _conversation(state="agent")
        session.scalar.return_value = conversation
        session.execute.side_effect = [
            _balance_result(
                [
                    SimpleNamespace(
                        contact_phone_number="5511999998888", credit_balance=Decimal("7")
                    )
                ]
            ),
            _balance_result([]),
        ]

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}", json={"state": "human"}
        )

        assert response.status_code == 200
        assert response.json()["end_customer_balance"] == 7.0
```

`test_resumo_devolve_saldo_do_cliente_final`:

```python
    def test_resumo_devolve_saldo_do_cliente_final(self, client, session, monkeypatch) -> None:
        conversation = _conversation(state="agent")
        session.scalar.return_value = conversation
        session.get = AsyncMock(return_value=SimpleNamespace(credit_balance=100))
        session.execute.side_effect = [
            _execute_returning([SimpleNamespace(sender_type="contact", content="oi")]),
            _balance_result(
                [
                    SimpleNamespace(
                        contact_phone_number="5511999998888", credit_balance=Decimal("15")
                    )
                ]
            ),
            _balance_result([]),
        ]
        monkeypatch.setattr(
            conversations_module,
            "generate_conversation_summary",
            AsyncMock(return_value={"summary": "Resumo.", "tokens_used": 100}),
        )
        pricing = SimpleNamespace(
            id=uuid.uuid4(),
            tokens_per_credit=1000,
            input_weight=Decimal("0.3"),
            output_weight=Decimal("1.0"),
        )
        monkeypatch.setattr(
            conversations_module, "get_current_pricing_config", AsyncMock(return_value=pricing)
        )

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/summary")

        assert response.status_code == 200
        assert response.json()["end_customer_balance"] == 15.0
```

`test_saldo_filtra_por_billing_habilitado`:

```python
    def test_saldo_filtra_por_billing_habilitado(self, client, session) -> None:
        session.execute.side_effect = [
            _execute_returning([_conversation()]),
            _balance_result([]),
            _balance_result([]),
        ]

        client.get("/api/v1/conversations")

        balance_query = session.execute.await_args_list[1].args[0]
        compiled = str(balance_query.compile(compile_kwargs={"literal_binds": True}))
        assert "tenant_billing_settings" in compiled
        assert "enabled IS true" in compiled
```

`test_lista_vazia_nao_consulta_saldo` **não muda** (sem conversas, `phone_numbers=[]`, tanto `_end_customer_balances_by_phone` quanto `_end_customer_cycles_by_phone` retornam antes de chamar `session.execute` — a asserção `assert_awaited_once()` continua correta).

- [ ] **Step 9: Rodar os testes e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 10: Rodar a suíte completa + lint**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v && uv run ruff check .`
Expected: todos passam (ou skip nos de integração que exigem Postgres real), lint limpo.

- [ ] **Step 11: Commit**

```bash
git add apps/api/app/schemas/conversations.py apps/api/app/api/v1/conversations.py apps/api/tests/unit/test_conversations_routes.py
git commit -m "feat(api): devolve o ciclo de créditos atual (total/consumido) do cliente final por conversa"
```

---

### Task 2: Frontend — indicador de ciclo na lista e na thread

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/components/ConversationList.tsx`
- Modify: `apps/web/src/components/ConversationThread.tsx`
- Test: `apps/web/__tests__/ConversationList.test.tsx`
- Test: `apps/web/__tests__/ConversationThread.test.tsx`

**Interfaces:**
- Consumes: `Conversation.end_customer_cycle_total`/`end_customer_cycle_consumed` (Task 1).
- Produces: nada consumido por outra task deste plano.

- [ ] **Step 1: Atualizar o tipo `Conversation`**

Em `apps/web/src/lib/types.ts`, troque:

```typescript
  end_customer_balance?: number | null;
```

por:

```typescript
  end_customer_balance?: number | null;
  end_customer_cycle_total?: number | null;
  end_customer_cycle_consumed?: number | null;
```

- [ ] **Step 2: Escrever os testes que falham**

Em `apps/web/__tests__/ConversationList.test.tsx`, adicione (depois do teste `"não mostra saldo quando end_customer_balance é null"`):

```tsx
  it("mostra o ciclo de créditos (comprado/consumido) quando presente", () => {
    render(
      <ConversationList
        conversations={[
          {
            ...conversations[0],
            end_customer_cycle_total: 200,
            end_customer_cycle_consumed: 20,
          },
        ]}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText("20 de 200 créditos usados")).toBeInTheDocument();
  });

  it("não mostra o ciclo quando end_customer_cycle_total é null", () => {
    render(
      <ConversationList
        conversations={[{ ...conversations[0], end_customer_cycle_total: null }]}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.queryByText(/usados/)).not.toBeInTheDocument();
  });
```

Em `apps/web/__tests__/ConversationThread.test.tsx`, adicione (depois do teste `"não mostra saldo do cliente quando end_customer_balance é null"`, mesmo padrão exato dos 2 testes vizinhos — `backendFetchMock.mockResolvedValue(jsonResponse([]))`, `onConversationUpdate`/`pollMs` nas props, `expect` síncrono sem `waitFor`):

```tsx
  it("mostra o ciclo de créditos (comprado/consumido) quando presente", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={{
          ...conversation("agent"),
          end_customer_cycle_total: 200,
          end_customer_cycle_consumed: 20,
        }}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    expect(screen.getByText(/20 de 200 créditos usados/)).toBeInTheDocument();
  });

  it("não mostra o ciclo quando end_customer_cycle_total é null", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={{ ...conversation("agent"), end_customer_cycle_total: null }}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    expect(screen.queryByText(/créditos usados/)).not.toBeInTheDocument();
  });
```

- [ ] **Step 3: Rodar e confirmar a falha**

Run: `cd apps/web && pnpm vitest run __tests__/ConversationList.test.tsx __tests__/ConversationThread.test.tsx`
Expected: FAIL nos 4 testes novos (o texto "usados" não existe ainda no código).

- [ ] **Step 4: Implementar em `ConversationList.tsx`**

Troque:

```tsx
                {conversation.end_customer_balance != null ? (
                  <span className="font-mono text-[11px] text-muted">
                    {formatCredits(conversation.end_customer_balance)} créditos
                  </span>
                ) : null}
```

por:

```tsx
                {conversation.end_customer_balance != null ? (
                  <span className="font-mono text-[11px] text-muted">
                    {formatCredits(conversation.end_customer_balance)} créditos
                  </span>
                ) : null}
                {conversation.end_customer_cycle_total != null ? (
                  <span className="font-mono text-[11px] text-muted">
                    {formatCredits(conversation.end_customer_cycle_consumed ?? 0)} de{" "}
                    {formatCredits(conversation.end_customer_cycle_total)} créditos usados
                  </span>
                ) : null}
```

- [ ] **Step 5: Implementar em `ConversationThread.tsx`**

Troque:

```tsx
          {conversation.end_customer_balance != null ? (
            <span className="font-mono text-xs text-muted">
              saldo do cliente: {formatCredits(conversation.end_customer_balance)} créditos
            </span>
          ) : null}
```

por:

```tsx
          {conversation.end_customer_balance != null ? (
            <span className="font-mono text-xs text-muted">
              saldo do cliente: {formatCredits(conversation.end_customer_balance)} créditos
            </span>
          ) : null}
          {conversation.end_customer_cycle_total != null ? (
            <span className="font-mono text-xs text-muted">
              {formatCredits(conversation.end_customer_cycle_consumed ?? 0)} de{" "}
              {formatCredits(conversation.end_customer_cycle_total)} créditos usados
            </span>
          ) : null}
```

- [ ] **Step 6: Rodar e confirmar sucesso**

Run: `cd apps/web && pnpm vitest run __tests__/ConversationList.test.tsx __tests__/ConversationThread.test.tsx`
Expected: todos os testes dos 2 arquivos passam.

- [ ] **Step 7: Rodar lint + build**

Run: `cd apps/web && pnpm lint && pnpm build`
Expected: sem erros novos.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/components/ConversationList.tsx apps/web/src/components/ConversationThread.tsx apps/web/__tests__/ConversationList.test.tsx apps/web/__tests__/ConversationThread.test.tsx
git commit -m "feat(web): mostra o ciclo de créditos do cliente final (comprado/consumido) na lista e na thread"
```

---

### Task 3: Frontend — `EndCustomerBillingTabs` (Configurações / Clientes / Consumo)

**Files:**
- Create: `apps/web/src/components/EndCustomerBillingTabs.tsx`
- Modify: `apps/web/src/components/EndCustomerBillingPanel.tsx`
- Modify: `apps/web/src/app/configuracoes/cobranca-clientes/page.tsx`
- Test: `apps/web/__tests__/EndCustomerBillingPanel.test.tsx`
- Test: `apps/web/__tests__/EndCustomerBillingTabs.test.tsx` (novo)

**Interfaces:**
- Consumes: `EndCustomerBillingPanel` (existente, sem mudança de props), `EndCustomerList` (existente), `ConversationsUsageReport` (existente — usado também pela Task 4, que remove seu outro consumidor).
- Produces: `EndCustomerBillingTabs` — consumido só pela página, não por outra task.

- [ ] **Step 1: Remover `EndCustomerList` de dentro de `EndCustomerBillingPanel.tsx`**

Em `apps/web/src/components/EndCustomerBillingPanel.tsx`, remova a linha de import:

```typescript
import { EndCustomerList } from "./EndCustomerList";
```

E remova a linha (perto do fim do JSX, depois do form de criar pacote):

```tsx
        {settings.enabled && <EndCustomerList />}
```

- [ ] **Step 2: Atualizar os testes de `EndCustomerBillingPanel.test.tsx`**

Remova os 2 testes que dependiam do `EndCustomerList` inline: `it("mostra a lista de clientes finais quando a cobrança está habilitada", ...)` e `it("não busca clientes finais quando a cobrança está desligada", ...)` — são os 2 últimos testes do arquivo.

- [ ] **Step 3: Rodar e confirmar que o `EndCustomerBillingPanel.test.tsx` restante passa**

Run: `cd apps/web && pnpm vitest run __tests__/EndCustomerBillingPanel.test.tsx`
Expected: todos os testes restantes passam (a remoção não afeta nenhum outro).

- [ ] **Step 4: Escrever o teste que falha para `EndCustomerBillingTabs`**

Crie `apps/web/__tests__/EndCustomerBillingTabs.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EndCustomerBillingTabs } from "@/components/EndCustomerBillingTabs";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

function mockRouting(enabled: boolean) {
  mockedFetch.mockImplementation(async (path: string) => {
    if (path === "end-customer-billing/settings") {
      return {
        ok: true,
        json: async () => ({
          enabled,
          billing_mode: "credits",
          stripe_secret_key_configured: false,
          stripe_webhook_secret_configured: false,
          end_customer_tokens_per_credit: null,
          webhook_url: "",
        }),
      };
    }
    if (path === "end-customer-billing/packages") return { ok: true, json: async () => [] };
    if (path === "end-customer-billing/customers") return { ok: true, json: async () => [] };
    if (path.startsWith("conversations/usage")) return { ok: true, json: async () => [] };
    return { ok: false, json: async () => null };
  });
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("EndCustomerBillingTabs", () => {
  it("começa na aba Configurações", async () => {
    mockRouting(false);

    render(<EndCustomerBillingTabs />);

    await waitFor(() => expect(screen.getByText(/secret key/i)).toBeInTheDocument());
  });

  it("esconde a aba Clientes quando a cobrança está desligada", async () => {
    mockRouting(false);

    render(<EndCustomerBillingTabs />);

    await waitFor(() => expect(screen.getByText(/secret key/i)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Clientes" })).not.toBeInTheDocument();
  });

  it("mostra a aba Clientes quando habilitado, e troca pra ela ao clicar", async () => {
    mockRouting(true);

    render(<EndCustomerBillingTabs />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Clientes" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Clientes" }));

    await waitFor(() =>
      expect(
        mockedFetch.mock.calls.some(([p]) => p === "end-customer-billing/customers"),
      ).toBe(true),
    );
  });

  it("aba Consumo mostra o relatório de conversas", async () => {
    mockRouting(false);

    render(<EndCustomerBillingTabs />);

    await waitFor(() => expect(screen.getByText(/secret key/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Consumo" }));

    await waitFor(() =>
      expect(screen.getByText("Nenhum consumo no período selecionado.")).toBeInTheDocument(),
    );
  });
});
```

- [ ] **Step 5: Rodar e confirmar a falha**

Run: `cd apps/web && pnpm vitest run __tests__/EndCustomerBillingTabs.test.tsx`
Expected: FAIL — `Cannot find module '@/components/EndCustomerBillingTabs'`.

- [ ] **Step 6: Implementar `EndCustomerBillingTabs.tsx`**

Crie `apps/web/src/components/EndCustomerBillingTabs.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";

import { ConversationsUsageReport } from "./ConversationsUsageReport";
import { EndCustomerBillingPanel } from "./EndCustomerBillingPanel";
import { EndCustomerList } from "./EndCustomerList";

type Tab = "config" | "clientes" | "consumo";

export function EndCustomerBillingTabs() {
  const [tab, setTab] = useState<Tab>("config");
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const response = await backendFetch("end-customer-billing/settings");
        if (response.ok) {
          const body = await response.json();
          setEnabled(Boolean(body.enabled));
        }
      } catch {
        // fail-safe: sem settings carregadas, a aba Clientes fica escondida
      }
    }
    void load();
  }, []);

  const tabClass = (active: boolean) =>
    `rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
      active ? "bg-ink text-ground" : "text-muted hover:text-ink"
    }`;

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex gap-1 border-b border-line px-8 py-3">
        <button
          type="button"
          onClick={() => setTab("config")}
          aria-pressed={tab === "config"}
          className={tabClass(tab === "config")}
        >
          Configurações
        </button>
        {enabled && (
          <button
            type="button"
            onClick={() => setTab("clientes")}
            aria-pressed={tab === "clientes"}
            className={tabClass(tab === "clientes")}
          >
            Clientes
          </button>
        )}
        <button
          type="button"
          onClick={() => setTab("consumo")}
          aria-pressed={tab === "consumo"}
          className={tabClass(tab === "consumo")}
        >
          Consumo
        </button>
      </div>

      {tab === "config" && <EndCustomerBillingPanel />}
      {tab === "clientes" && enabled && <EndCustomerList />}
      {tab === "consumo" && <ConversationsUsageReport />}
    </div>
  );
}
```

- [ ] **Step 7: Ligar na página**

Em `apps/web/src/app/configuracoes/cobranca-clientes/page.tsx`, troque:

```tsx
import { EndCustomerBillingPanel } from "@/components/EndCustomerBillingPanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default function ConfiguracoesCobrancaClientesPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="cobranca" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <EndCustomerBillingPanel />
      </div>
    </div>
  );
}
```

por:

```tsx
import { EndCustomerBillingTabs } from "@/components/EndCustomerBillingTabs";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default function ConfiguracoesCobrancaClientesPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="cobranca" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <EndCustomerBillingTabs />
      </div>
    </div>
  );
}
```

- [ ] **Step 8: Rodar e confirmar sucesso**

Run: `cd apps/web && pnpm vitest run __tests__/EndCustomerBillingTabs.test.tsx __tests__/EndCustomerBillingPanel.test.tsx`
Expected: todos os testes dos 2 arquivos passam.

- [ ] **Step 9: Rodar lint + build**

Run: `cd apps/web && pnpm lint && pnpm build`
Expected: sem erros novos.

- [ ] **Step 10: Commit**

```bash
git add apps/web/src/components/EndCustomerBillingTabs.tsx apps/web/src/components/EndCustomerBillingPanel.tsx apps/web/src/app/configuracoes/cobranca-clientes/page.tsx apps/web/__tests__/EndCustomerBillingPanel.test.tsx apps/web/__tests__/EndCustomerBillingTabs.test.tsx
git commit -m "feat(web): consolida configurações, clientes e consumo do cliente final em abas"
```

---

### Task 4: Frontend — remover a aba "Consumo" de `/conversas`

**Files:**
- Modify: `apps/web/src/components/ConversationsPanel.tsx`
- Test: `apps/web/__tests__/ConversationsPanel.test.tsx`

**Interfaces:**
- Consumes: nada (a Task 3 já criou o novo lar do `ConversationsUsageReport` — esta task só remove o antigo).
- Produces: nada — última task do plano.

- [ ] **Step 1: Remover o teste da aba Consumo**

Em `apps/web/__tests__/ConversationsPanel.test.tsx`, remova o teste `"aba Consumo mostra o relatório e não busca conversations?origin="`.

- [ ] **Step 2: Rodar e confirmar que o restante ainda passa**

Run: `cd apps/web && pnpm vitest run __tests__/ConversationsPanel.test.tsx`
Expected: os testes restantes passam (a remoção do teste não quebra nada — é o código que ainda precisa mudar no próximo Step).

- [ ] **Step 3: Remover a aba do componente**

Em `apps/web/src/components/ConversationsPanel.tsx`, remova o import:

```typescript
import { ConversationsUsageReport } from "./ConversationsUsageReport";
```

Troque:

```typescript
type Tab = "real" | "test" | "usage";
```

por:

```typescript
type Tab = "real" | "test";
```

Troque (dentro de `loadConversations`):

```typescript
  const loadConversations = useCallback(async () => {
    if (tab === "usage") return;
    try {
```

por:

```typescript
  const loadConversations = useCallback(async () => {
    try {
```

Troque (dentro do `useEffect` de polling):

```typescript
  useEffect(() => {
    if (tab === "usage") return;
    setLoaded(false);
```

por:

```typescript
  useEffect(() => {
    setLoaded(false);
```

Remova o botão "Consumo" do header (entre o botão "Testes" e o `</div>` que fecha o grupo de botões):

```tsx
          <button
            type="button"
            onClick={() => switchTab("usage")}
            aria-pressed={tab === "usage"}
            className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
              tab === "usage" ? "bg-ink text-ground" : "text-muted hover:text-ink"
            }`}
          >
            Consumo
          </button>
```

Troque o bloco condicional do corpo:

```tsx
      {tab === "usage" ? (
        <ConversationsUsageReport />
      ) : (
        <div className="flex min-h-0 min-w-0 flex-1">
```

por:

```tsx
      <div className="flex min-h-0 min-w-0 flex-1">
```

E, no fechamento correspondente (o `</div>` que fechava esse bloco, seguido do `)}`), troque:

```tsx
        </div>
      )}
```

por:

```tsx
        </div>
```

(confira que a indentação do JSX interno entre esses dois pontos continua consistente — como o `<div>` deixou de estar dentro de um ternário, o nível de indentação pode precisar de ajuste visual, mas isso não afeta o comportamento.)

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/web && pnpm vitest run __tests__/ConversationsPanel.test.tsx`
Expected: todos os testes passam.

- [ ] **Step 5: Rodar lint + build**

Run: `cd apps/web && pnpm lint && pnpm build`
Expected: sem erros novos (confirma que `ConversationsUsageReport` não ficou importado sem uso, e que não sobrou nenhuma referência a `"usage"` órfã — TypeScript pegaria isso).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/ConversationsPanel.tsx apps/web/__tests__/ConversationsPanel.test.tsx
git commit -m "feat(web): remove a aba Consumo de /conversas — agora vive em /configuracoes/cobranca-clientes"
```
