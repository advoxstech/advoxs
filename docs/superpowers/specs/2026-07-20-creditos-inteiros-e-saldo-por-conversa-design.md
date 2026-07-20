# Créditos inteiros + saldo do cliente final por conversa — Design

## Contexto

Duas mudanças independentes, aprovadas juntas pelo usuário:

1. Desde a Etapa 1/2 da wallet unificada, o consumo de créditos é calculado por uma fórmula ponderada (`tokens_ponderados = tokens_input × input_weight + tokens_output × output_weight`, depois `/ tokens_per_credit`) e arredondado a **4 casas decimais**. O usuário não quer mais casas decimais nos créditos — os descontos devem ser sempre números inteiros.
2. O painel de conversas (`/conversas`) não mostra, em nenhum lugar, quanto de saldo resta pro cliente final de cada conversa (quando a cobrança do cliente final está habilitada) — o usuário quer essa informação visível por conversa.

## 1. Créditos sempre inteiros

### Abordagem escolhida

Trocar o arredondamento final de `calcular_creditos` de 4 casas decimais para **0 casas decimais**, mantendo a mesma fórmula ponderada e o mesmo `ROUND_HALF_UP`. Nenhuma migration de schema — as colunas `Numeric(12,4)` continuam como estão (suportam armazenar um valor inteiro sem problema), só o valor calculado passa a ser sempre um número inteiro dali em diante.

**Sem mínimo de 1 crédito por resposta.** Uma execução muito barata (poucos tokens ponderados) pode arredondar pra **0 créditos** — mantém a filosofia atual de nunca cobrar a mais por fração, só que agora aplicada ao inteiro mais próximo em vez de à quarta casa decimal. Consequência aceita: respostas muito curtas podem ser efetivamente gratuitas.

**Sem migração de histórico.** Transações já lançadas com valores fracionados continuam como estão no ledger — só o cálculo de novas cobranças muda.

### O que muda

- `apps/api/app/services/pricing.py` e `apps/worker/app/pricing.py` (função `calcular_creditos`, espelhada nos dois serviços): trocar `_PRECISION = Decimal("0.0001")` por `_PRECISION = Decimal("1")`. Docstring atualizada (deixa de dizer "4 casas").
- Nenhuma mudança de schema, nenhuma mudança nos pontos que chamam `calcular_creditos` (assinatura da função não muda).
- `CLAUDE.md`, seção "Regra de consumo": atualizar a frase "arredondado a 4 casas decimais" para refletir arredondamento pro inteiro mais próximo, e mencionar explicitamente que respostas muito baratas podem arredondar pra 0.

### Testes afetados (valores precisam ser recalculados)

- `apps/api/tests/unit/test_pricing_service.py` e `apps/worker/tests/unit/test_pricing.py`: todos os `assert calcular_creditos(...) == Decimal("X.XXXX")` precisam do novo valor esperado (inteiro). O teste que hoje verifica o arredondamento de 4 casas precisa ser reescrito pra verificar o arredondamento pro inteiro mais próximo (casos de fronteira: `.5` sobe, `.49` desce).
- `apps/api/tests/unit/test_conversations_routes.py` (`TestGenerateSummary::test_gera_resumo_persiste_e_debita_creditos`): valor esperado de `amount_credits` recalculado.
- `apps/api/tests/unit/test_test_conversations_routes.py` (`test_fluxo_feliz_persiste_e_debita`): idem.
- `apps/worker/tests/unit/test_process_inbound_message.py` (`test_consumo_ponderado_fracionado`): valor recalculado e nome do teste ajustado (não é mais fracionado).

## 2. Saldo do cliente final por conversa

### Abordagem escolhida

Mostrar, em cada conversa (lista + cabeçalho da thread), o saldo de créditos do **cliente final** daquele contato especificamente — não o saldo do escritório (`tenants.credit_balance`), que é global e já visível em outros lugares (banner, `/creditos`, `/inicio`). Só aparece quando a cobrança do cliente final está habilitada pro tenant (`tenant_billing_settings.enabled = true`) — sem essa feature habilitada, não existe um "saldo por conversa" pra mostrar.

### O que muda

**Backend** (`apps/api/app/api/v1/conversations.py`):
- `ConversationOut` (`apps/api/app/schemas/conversations.py`) ganha um campo novo, opcional: `end_customer_balance: float | None = None`.
- Nova função auxiliar que busca o saldo de `end_customer_balances` por `contact_phone_number`, retornando `{}` se a cobrança do cliente final não estiver habilitada pro tenant — usada tanto no caminho em lote (`list_conversations`, evita N+1) quanto nos caminhos de uma única conversa (`update_state`, `generate_summary`).
- As três rotas que devolvem `ConversationOut` (`list_conversations`, `update_state`, `generate_summary`) passam a preencher esse campo.
- `test_conversations.py` (rota `create_test_conversation`) não muda — conversas de teste usam contato sintético, nunca têm saldo de cliente final; o campo cai no default `None`.

**Frontend**:
- `lib/types.ts`: `Conversation.end_customer_balance?: number | null` — opcional, pra não exigir tocar em nenhuma fixture de teste já existente.
- `ConversationList.tsx`: quando `end_customer_balance != null`, mostra o saldo (via `formatCredits`) na mesma linha do indicador de estado (agente/manual), alinhado à direita.
- `ConversationThread.tsx`: quando `end_customer_balance != null`, mostra o saldo no cabeçalho da thread, perto do número de telefone.
- `TestConversationThread.tsx` não muda (conversas de teste nunca têm esse campo preenchido).

O valor já chega atualizado a cada ciclo de polling existente do painel (`GET /conversations`, já pollado a cada 5s) — sem polling novo.

## Fora de escopo

- Migração/normalização de saldos ou lançamentos históricos fracionados.
- Mínimo de 1 crédito por cobrança (decisão explícita: pode arredondar pra 0).
- Mostrar o saldo do escritório (`tenants.credit_balance`) repetido por conversa — já é visível em outros lugares do painel.
- Qualquer mudança na base de conhecimento por categoria/agente (item 3 do pedido original, tratado como projeto separado, maior).

## Testes

- **Backend**: novos/atualizados em `test_pricing_service.py`, `test_pricing.py` (worker), `test_conversations_routes.py` (valor recalculado + novos testes de `end_customer_balance` presente/ausente em `list_conversations`), `test_test_conversations_routes.py` (valor recalculado), `test_process_inbound_message.py` (valor recalculado + rename).
- **Frontend**: novos testes em `ConversationList.test.tsx` e `ConversationThread.test.tsx` cobrindo a exibição do saldo quando presente e a ausência quando `null`/`undefined`.
