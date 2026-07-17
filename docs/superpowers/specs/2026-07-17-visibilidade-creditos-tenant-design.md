# Visibilidade de créditos para o tenant — Design

## Contexto

Depois da fundação (Etapa 1: `pricing_configs`, créditos fracionados, auditoria de tokens) e do consumo ponderado com moeda única (Etapa 2), o tenant continua sem visibilidade real sobre o próprio gasto: `/inicio` e `/creditos` só mostram saldo atual e um agregado cego de 30 dias. Não existe extrato, não existe breakdown por conversa, e quando a cobrança do cliente final está ativa o tenant não vê nada sobre os próprios clientes. Isso é a Etapa 5 (Visibilidade) do plano original de wallet unificada.

Este documento cobre as 3 peças definidas com o usuário:

1. **Extrato geral** do tenant (compras + consumo) em `/creditos`.
2. **Consumo por conversa**, nova aba em `/conversas`.
3. **Saldo/consumo dos clientes finais**, nova seção em `/configuracoes/cobranca-clientes`.

## Regra transversal: nunca falar em "tokens" com o tenant

Todo texto, rótulo de coluna e mensagem de erro voltado ao painel do escritório usa **crédito**, nunca "token". Tokens são detalhe de custo interno (auditado em `messages`/`credit_transactions` desde a Etapa 1), não vocabulário de produto.

⚠️ **Achado durante o design**: as descrições já gravadas no ledger hoje mencionam tokens explicitamente — `"Consumo do agente ({tokens_used} tokens)"` (`apps/worker/app/tasks/messages.py`, duas ocorrências), `"Resumo de conversa gerado ({tokens_used} tokens)"` (`apps/api/app/api/v1/conversations.py`) e `"Consumo do agente em conversa de teste ({tokens_used} tokens)"` (`apps/api/app/services/test_conversations.py`). Como o extrato (peça 1) exibe `description` diretamente, essas strings **precisam ser corrigidas antes** de expor o extrato — senão o primeiro recurso tenant-facing dessa auditoria já vaza a palavra "tokens". Nenhum teste hoje assere o texto exato dessas descrições (confirmado por grep), então a correção é de baixo risco.

## Peça 1 — Extrato geral (`/creditos`)

**Onde:** nova seção abaixo do saldo e dos pacotes, na mesma página `/creditos` (`apps/web/src/app/creditos/page.tsx`). Componente novo `CreditosExtrato.tsx`, isolado do `CreditosPanel.tsx` existente (que já cuida de saldo + compra).

**API:** `GET /api/v1/billing/transactions?limit=&offset=` (`apps/api/app/api/v1/billing.py`, tenant-scoped via `get_tenant_session`).
- Resposta: lista de `{id, type, amount_credits: float, description: str|None, created_at}`, ordenada por `created_at desc`.
- `limit` default 50, máximo 200 (padrão já usado em outras listagens); `offset` default 0.
- Fonte: tabela `credit_transactions` (ledger), sem filtro de tipo/data na v1.

**Frontend:** lista simples (sem paginação visível na v1 — busca uma página de até 50 itens, mesmo padrão minimalista já usado em `ConversationsPanel`, que também não pagina na UI). Cada linha mostra tipo traduzido (mapa `purchase→Compra`, `consumption→Consumo`, `resale→Revenda`, `adjustment→Ajuste`, `refund→Reembolso`, `bonus→Bônus`), valor via `formatCredits`, descrição (já limpa de tokens) e data via `formatFullDateTime`.

## Peça 2 — Consumo por conversa (nova aba em `/conversas`)

**Onde:** hoje `ConversationsPanel.tsx` já tem 2 abas ("Conversas"/"Testes") que trocam o `origin` da lista mestre-detalhe. A 3ª aba ("Consumo") não é mestre-detalhe — é uma tabela de relatório em largura cheia. Isso exige reestruturar o componente: os botões de aba sobem para uma barra comum no topo, e o corpo troca entre o layout atual (lista + thread) e o novo relatório, dependendo da aba ativa.

**API:** `GET /api/v1/conversations/usage?from=&to=&limit=&offset=` (`apps/api/app/api/v1/conversations.py`).
- `from`/`to`: datas obrigatórias (`YYYY-MM-DD`), intervalo inclusivo em UTC. `to < from` → `422`.
- Fonte: `messages` (não o ledger) — cada mensagem de agente com `credits_consumed IS NOT NULL` já carrega o custo da execução que ela representa (é assim que o worker grava hoje: só a primeira mensagem de cada resposta em lote leva o custo). Agrega por `conversation_id`, somando `credits_consumed` e contando as linhas nesse filtro (== nº de execuções cobradas no período), com `MAX(created_at)` como última atividade.
- ⚠️ **Limitação conhecida, aceita para a v1**: o resumo de conversa sob demanda (`POST /conversations/{id}/summary`) grava o consumo só no ledger (`credit_transactions`, com `related_message_id=None`), sem criar uma linha em `messages`. Como esse relatório lê de `messages`, o custo de um resumo **não aparece** na conversa correspondente aqui — ele só aparece no extrato geral (peça 1), sem atribuição a uma conversa específica. É uma lacuna pré-existente do modelo de dados (o ledger não tem `conversation_id` direto), não uma regressão desta feature — documentado, não resolvido agora.
- Resposta: lista de `{conversation_id, contact_phone_number, is_test, credits_consumed: float, billed_responses: int, last_message_at}`, ordenada por `credits_consumed desc` (mostra primeiro quem custou mais no período). `limit`/`offset` mesmo padrão da peça 1. **Nota de nomenclatura**: `billed_responses` ≠ `usage_last_30_days.agent_messages` do dashboard — este conta execuções com custo registrado (`credits_consumed IS NOT NULL`), aquele conta toda mensagem `sender_type=agent`. Nomes diferentes de propósito para não confundir semânticas na hora de implementar.

**Frontend:** componente novo `ConversationsUsageReport.tsx`. Filtro de data com presets (7/30/90 dias, default 30) + intervalo customizado (dois inputs de data). Tabela: contato (`formatPhone`), créditos consumidos (`formatCredits`), `billed_responses` exibido com o rótulo "Respostas do agente" (mesmo termo já usado no dashboard, ainda que a contagem subjacente seja de execuções cobradas — ver nota de nomenclatura acima), última atividade (`formatMessageTime`/`formatFullDateTime`). Conversas de teste aparecem com um badge "teste" ao lado do contato — não ficam escondidas, mas se distinguem visualmente.

## Peça 3 — Saldo/consumo dos clientes finais (`/configuracoes/cobranca-clientes`)

**Onde:** nova seção dentro do `EndCustomerBillingPanel.tsx` existente, abaixo do CRUD de pacotes. Só renderiza quando `settings.enabled === true` (sem sentido mostrar clientes finais numa feature desligada). Nenhuma mudança em `TenantNav` nem no lado do WhatsApp/`agents` — a secretária já cuida de checar saldo e oferecer pacotes ao cliente final; essa peça é puramente uma tela do tenant.

**API:** `GET /api/v1/end-customer-billing/customers?limit=&offset=` (`apps/api/app/api/v1/end_customer_billing.py`).
- Fonte: `end_customer_balances` (saldo atual, uma linha por contato) + agregação de `end_customer_credit_transactions` por `contact_phone_number` (soma de `purchase` = total comprado, soma absoluta de `consumption` = total consumido).
- Resposta: lista de `{contact_phone_number, credit_balance: float, total_purchased: float, total_consumed: float}`, ordenada por `total_consumed desc`.

**Frontend:** componente novo `EndCustomerList.tsx`. Tabela: contato (`formatPhone`), saldo atual, total comprado, total consumido — todos via `formatCredits`.

## Fora de escopo (explícito)
- Filtro por tipo/data no extrato geral (peça 1) — fica pra depois se for pedido.
- Qualquer comando ou tela no WhatsApp para o cliente final consultar o próprio saldo — já é responsabilidade da secretária hoje.
- Corrigir a lacuna do resumo sob demanda não aparecer no relatório por conversa (documentada acima).
- Paginação visível na UI das 3 listas — os endpoints aceitam `limit`/`offset`, mas o frontend busca uma página só (até 50/100 itens) na v1, sem botão "carregar mais".

## Testes
- **Backend**: um teste de unidade por endpoint novo cobrindo o caminho feliz (agregação correta) e os casos de borda relevantes (extrato vazio; `to < from` → 422; cliente final sem nenhuma transação ainda). Teste de regressão garantindo que nenhuma `description` gravada por `_debitar_creditos`/`_debitar_creditos_cliente_final`/rota de resumo/`test_conversations.py` contém a palavra "tokens".
- **Frontend**: teste de render para cada componente novo (estado vazio, estado com dados, formatação em créditos). Para `ConversationsPanel.tsx`, atualizar os testes existentes para a nova estrutura de abas (3 botões, troca de view) sem perder cobertura das duas abas atuais.
