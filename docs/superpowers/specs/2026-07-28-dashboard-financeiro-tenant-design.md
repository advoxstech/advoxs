# Dashboard financeiro do tenant — Design

## Contexto

O tenant hoje tem visibilidade fragmentada do próprio dinheiro:
- `ConnectEarnings` (recém-implementado): saldo disponível/pendente e últimos repasses da própria conta Stripe Connect — é a **posição de caixa**, lida ao vivo da Stripe.
- `EndCustomerList`: saldo atual + total comprado/consumido por cliente final — **acumulado desde o início**, sem filtro de período.
- `CreditosExtrato` (`/creditos`): ledger completo de compras do tenant com a Advoxs — cronológico, sem agregação nem gráfico.

Falta uma visão de **faturamento por período**: quanto entrou de venda pros clientes finais no mês, com detalhamento por cliente e um gráfico — e o espelho do outro lado, quanto o próprio tenant gastou comprando créditos da Advoxs no mesmo tipo de recorte.

## Escopo

1. **Faturamento do cliente final** — nova aba "Faturamento" em `/configuracoes/cobranca-clientes`, só para tenants `billing_provider="connect"` (mesma restrição já aplicada à assinatura recorrente — standalone é tratado como transitório em toda a plataforma, não recebe funcionalidade nova).
2. **Gasto do tenant com créditos da Advoxs** — gráfico mensal adicionado em `/creditos`, ao lado do extrato já existente.

Fora de escopo desta entrega: reembolsos (receita bruta só, decisão deliberada — o fluxo de reembolso via Stripe Connect pro cliente final nem existe ainda na plataforma), suporte a tenants `standalone`, exportação (CSV/PDF).

## Achado que mudou o desenho original

Investigação: assinaturas mensais recorrentes (`process_end_customer_subscription_created`/`_renewed`, já implementadas) **nunca escrevem em `end_customer_credit_transactions`** — só compra avulsa e ajuste manual gravam nesse ledger. Isso significa que, hoje, a receita de assinatura é invisível no nosso banco.

Também investigado e descartado: ler a receita direto da Stripe (`BalanceTransaction`/`Charge`) e cruzar com `contact_phone_number` via metadata — a Stripe **não propaga automaticamente** a metadata da Checkout Session/Subscription pra cada fatura de renovação mensal (confirmado contra a doc atual da API). Tentar reconciliar isso via metadata seria frágil exatamente no caso que mais importa (renovação).

**Decisão**: em vez de ler a Stripe ao vivo pra este relatório, criamos uma tabela nova que registra cada pagamento de assinatura (criação + cada renovação) no nosso próprio banco — o mesmo princípio que já vale pra compra avulsa (`end_customer_credit_transactions`), só que pra assinatura. O relatório de faturamento passa a ser 100% uma agregação do nosso banco (2 tabelas: compra avulsa + pagamento de assinatura), nunca uma chamada à Stripe em tempo de leitura — mais simples, mais rápido, sem depender de paginação/reconciliação com a API externa. `ConnectEarnings` continua sendo a única funcionalidade que lê a Stripe ao vivo (pergunta diferente: "quanto dinheiro real eu tenho", não "quanto vendi").

## Modelo de dados

### `end_customer_subscription_payments` (tenant-scoped, nova tabela)

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | uuid, PK | |
| `tenant_id` | uuid, FK → `tenants` | |
| `contact_phone_number` | String | |
| `end_customer_subscription_id` | uuid, FK → `end_customer_subscriptions` | |
| `amount_brl` | Numeric(10,2) | Valor do pagamento — `package.price_brl` no momento do pagamento |
| `stripe_invoice_id` | String, unique | Idempotência — `invoice.payment_succeeded` pode ser reentregue pela Stripe |
| `paid_at` | timestamptz | |

RLS habilitada (`tenant_isolation`), mesmo padrão de toda tabela tenant-scoped deste domínio (ver migration `0022`, que teve exatamente esse item esquecido e corrigido numa revisão anterior desta mesma sessão — não repetir o erro).

### Escrita: estender `process_end_customer_subscription_renewed`

`invoice.payment_succeeded` já firma tanto a **primeira cobrança** de uma assinatura nova quanto **cada renovação** — não precisa de um segundo ponto de escrita em `process_end_customer_subscription_created`. A função (já existe, já resolve `_extract_subscription_id`/`_extract_period_end`) ganha, além da atualização de `status`/`current_period_end` que já faz, um `INSERT` idempotente (por `stripe_invoice_id`) em `end_customer_subscription_payments`, usando `package.price_brl` resolvido via `EndCustomerSubscription.end_customer_credit_package_id`.

**Limitação conhecida, aceita de propósito** (mesmo espírito da tolerância a `current_period_end IS NULL` já documentada): se `invoice.payment_succeeded` da primeira cobrança chegar **antes** de `checkout.session.completed` (ordem não garantida pela Stripe), a função já retorna sem processar (`EndCustomerSubscription` ainda não existe) — esse primeiro pagamento específico não seria registrado no histórico de faturamento. Janela estreita, mesma classe de corrida já tolerada em produção nesta feature; não é resolvida aqui.

## Backend

### `GET /api/v1/end-customer-billing/revenue?from=YYYY-MM-DD&to=YYYY-MM-DD`

- 404 se o tenant não é `billing_provider="connect"` (mesmo padrão de `connect-account/earnings`).
- Duas agregações, cada uma sobre uma tabela (sem `UNION` SQL — mais simples de testar): `end_customer_credit_transactions` (`type="purchase"`, join com `end_customer_credit_packages.price_brl`) e `end_customer_subscription_payments`, ambas filtradas por `created_at`/`paid_at` no intervalo.
- Merge em Python por `(mês, contact_phone_number)`.
- Resposta: `{by_month: [{month: "2026-07", total_brl: float}], by_customer: [{contact_phone_number: str, total_brl: float}]}` — `by_customer` ordenado por `total_brl` desc.
- Mesma ressalva já documentada no CLAUDE.md pro dashboard de admin: `price_brl`/`amount_brl` refletem o preço do pacote **no momento da consulta** pra compra avulsa (a tabela de transação não guarda o preço pago), mas pra assinatura é o valor real gravado no momento do pagamento (`end_customer_subscription_payments.amount_brl`) — mais preciso que o lado avulso, sem retrabalho pra igualar.

### Gasto do tenant (`/creditos`)

Endpoint novo ou extensão do existente de extrato — decisão de implementação, não afeta o desenho: `GET /api/v1/billing/spending?from=&to=` agregando `credit_transactions` (`type="purchase"`, join `credit_packages.price_brl`) por mês. Mesma ressalva de preço atual vs. histórico.

## Frontend

- **Aba "Faturamento"** (`EndCustomerBillingTabs`, ao lado de Configurações/Clientes/Consumo): filtro de período reaproveitando o componente de presets já usado em `ConversationsUsageReport` (7/30/90 dias + intervalo customizado, default 30); gráfico de barras mensal (componente novo, seguindo o padrão SVG à mão já estabelecido em `NewTenantsChart.tsx` — sem nova dependência de biblioteca); tabela por cliente ordenada por valor desc.
- **`/creditos`**: gráfico de barras mensal do gasto, mesmo padrão de componente, posicionado acima ou ao lado do `CreditosExtrato` já existente.

## Dependência operacional

A chave restrita `STRIPE_CONNECT_SECRET_KEY` **não precisa de nenhuma permissão nova** pra esta feature — diferente do `ConnectEarnings` (que lê Balance/Payouts ao vivo da Stripe), este relatório é 100% dados do nosso próprio banco.

## Fora de escopo

- Reembolsos (receita bruta só).
- Tenants `billing_provider="standalone"`.
- Exportação (CSV/PDF).
- Reconciliação ao vivo com a Stripe (o relatório é baseado no nosso ledger, não num pull em tempo real).
