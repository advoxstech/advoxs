# Assinatura mensal recorrente para o cliente final — Design

## Contexto

Além da compra de créditos avulsos (já implementada — ver seção "Cobrança do cliente final" do `CLAUDE.md`), o tenant poderá oferecer ao próprio cliente final uma **assinatura mensal recorrente** como alternativa: em vez de comprar um lote de créditos, o cliente final paga um valor fixo por mês e tem acesso **ilimitado** ao atendimento dos agentes, sem consumir créditos.

Direção já dada pelo usuário (decisões, não pontos abertos):
1. A opção de assinatura aparece na **mesma lista** de pacotes que o cliente final já vê no billing gate determinístico via WhatsApp (mensagem interativa `list`), como mais uma linha, identificada como mensal/recorrente.
2. Reaproveita o **mesmo webhook** já configurado por tenant — no caso desta feature, o webhook único de Stripe Connect (`POST /webhooks/stripe/connect`), já implementado.
3. O tenant cria o plano recorrente pelo próprio painel, do mesmo jeito que já cria pacotes de crédito avulsos hoje (`/configuracoes/cobranca-clientes`, CRUD em `end_customer_credit_packages`).

### Dependência resolvida: interação com Stripe Connect

Esta feature ficou deliberadamente parada até a migração da cobrança do cliente final para Stripe Connect (`docs/superpowers/specs/2026-07-24-stripe-connect-cobranca-cliente-final-design.md`, já implementada e mergeada) estar concluída — implementá-la antes seria retrabalho.

Pesquisa feita para destravar o desenho:
- **Direct charge com `mode="subscription"` funciona exatamente como o pagamento único já implementado**: mesmo `stripe_account=<acct_id>` (Direct charge), mesma Checkout Session, só troca `mode="payment"` por `mode="subscription"` e adiciona `price_data.recurring={"interval": "month"}` no line item. A complexidade de "cliente v2 na conta conectada" (`customer_account`, mencionada na doc da Stripe para Connect+Subscriptions) só se aplica quando é a **plataforma** cobrando a **conta conectada** (ex: taxa de SaaS da Advoxs sobre o tenant) — não é o caso aqui, que é o tenant cobrando o próprio cliente final. Essa complicação não existe pra este desenho.
- **Todos os eventos do ciclo de vida da assinatura** (`invoice.payment_succeeded` a cada renovação, `customer.subscription.updated`/`.deleted` no cancelamento) chegam pelo **mesmo webhook único de Connect já implementado** (`POST /webhooks/stripe/connect`, resolve o tenant via `event["account"]`) — não é preciso nenhum endpoint novo nem nenhuma configuração adicional no Dashboard da Stripe da plataforma.

### Decisão de escopo resultante

**Esta feature só existe para tenants com `billing_provider="connect"`.** Tenants ainda em `billing_provider="standalone"` não veem a opção de criar planos recorrentes. Motivo: o webhook antigo por tenant (`POST /webhooks/stripe/tenant/{tenant_id}`) só trata `checkout.session.completed` — extendê-lo pros 3 eventos novos exigiria cada tenant `standalone` reconfigurar manualmente o Dashboard da própria Stripe, o que contradiz o motivo de existir o Connect (nenhuma configuração manual do lado do tenant). Não é uma limitação nova desta feature — é consequência direta de `standalone` já ser tratado como estado transitório (ver spec do Connect).

## Modelo de dados

### `end_customer_credit_packages` — ganha 1 coluna

| Coluna | Tipo | Descrição |
|---|---|---|
| `kind` | `String`, default `"one_time"` | `"one_time"` (pacote de créditos avulso, comportamento atual) ou `"subscription"` (assinatura mensal recorrente). |

`credits_granted` deixa de ser obrigatório no schema (`EndCustomerCreditPackageIn`/`Update`) quando `kind="subscription"` — um plano recorrente não concede um lote fixo de créditos, concede acesso ilimitado enquanto ativo. Validação: `credits_granted` obrigatório (`gt=0`) quando `kind="one_time"`; ignorado/`None` quando `kind="subscription"`.

**Por que reaproveitar esta tabela em vez de criar uma tabela "planos" separada**: o CRUD, a listagem, e a apresentação no billing gate já existem inteiramente pra esta tabela — duplicar em uma tabela nova replicaria toda essa infraestrutura pra uma diferença que é só "um campo, dois valores possíveis". `kind` é deliberadamente diferente de `tenant_billing_settings.billing_mode` (coluna já existente, mas tenant-wide e semanticamente distinta — decide o modo de cobrança geral do tenant, não o tipo de um pacote específico).

**Restrição de escopo**: `POST /end-customer-billing/packages`/`PATCH .../{id}` rejeitam (`409`, mesmo padrão de erro já usado nesta rota) `kind="subscription"` quando `tenant_billing_settings.billing_provider != "connect"`.

### `end_customer_subscriptions` (tenant-scoped) — tabela nova

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | uuid, PK | |
| `tenant_id` | uuid, FK → `tenants` | |
| `contact_phone_number` | String | |
| `end_customer_credit_package_id` | uuid, FK → `end_customer_credit_packages`, nullable | plano assinado (nullable pra sobreviver a uma exclusão futura do plano, mesmo padrão de outras FKs deste domínio) |
| `stripe_subscription_id` | String, unique | id da Subscription na Stripe (dentro da conta conectada do tenant) |
| `status` | String | `"active"` \| `"canceled"` \| `"past_due"` |
| `current_period_end` | DateTime, nullable | fim do ciclo pago atual — atualizado a cada `invoice.payment_succeeded` |
| `created_at`, `updated_at` | DateTime | |

`UNIQUE (tenant_id, contact_phone_number)` — um cliente final tem, no máximo, 1 assinatura ativa por tenant (comprar uma segunda assinatura enquanto a primeira está ativa não é um cenário suportado nesta v1; fora de escopo).

É o equivalente, pra assinatura, do que `end_customer_balances` é pro saldo de créditos — tabela própria, separada do catálogo (`end_customer_credit_packages`).

## Checkout

`create_end_customer_checkout_session` (`apps/api/app/services/end_customer_billing.py`) ganha uma ramificação por `package.kind`, dentro do ramo `billing_provider == "connect"` já existente (pacotes `kind="subscription"` só existem em tenants connect, ver "Restrição de escopo" acima — a função pode assumir isso, mas mantém a defesa já existente de `stripe_account_status == "active"`):

- `kind="one_time"`: comportamento atual, inalterado (`mode="payment"`).
- `kind="subscription"`: `mode="subscription"`, line item com `price_data.recurring={"interval": "month"}` em vez de um item de quantidade única; metadata ganha `kind="end_customer_subscription"` (valor novo, ao lado do já existente `"end_customer_purchase"`) em vez do nome do pacote genérico.

Mesmo `stripe_account=<acct_id>` (Direct charge), mesma chave (`settings.stripe_connect_secret_key`), sem `application_fee_amount` — nenhuma mudança na mecânica de Direct charge já implementada, só no `mode`/line item.

## Webhook (`POST /webhooks/stripe/connect`)

`apps/api/app/api/v1/webhooks/stripe_connect.py` ganha 3 branches novos de `event["type"]`, ao lado dos já existentes (`checkout.session.completed` pra `kind="end_customer_purchase"`, `account.updated`):

1. **`checkout.session.completed` com `metadata.kind == "end_customer_subscription"`** (nova função `process_end_customer_subscription_created`, em `apps/api/app/services/end_customer_billing.py`, espelhando `process_end_customer_checkout_completed`): cria a linha em `end_customer_subscriptions` (`stripe_subscription_id` = `stripe_session["subscription"]`, `status="active"`), idempotente por `stripe_subscription_id` (mesmo padrão de idempotência por id já usado pra compra avulsa). Envia confirmação via WhatsApp: **"Assinatura ativada! Você já tem acesso ilimitado."** — mesmo padrão best-effort de `_send_purchase_confirmation` (falha no envio não desfaz a criação da assinatura, que já foi commitada).
2. **`invoice.payment_succeeded`** (renovação mensal): atualiza `current_period_end`/`status="active"` na linha existente (resolvida por `stripe_subscription_id` = `event["data"]["object"]["subscription"]`). **Sem notificação ao cliente** — decisão deliberada, renovação silenciosa evita spam mensal.
3. **`customer.subscription.deleted`** (cancelamento) e **`customer.subscription.updated`** (ex: `past_due`): atualiza `status` na linha existente. Cancelamento (`.deleted`) dispara aviso via WhatsApp: **"Sua assinatura mensal foi cancelada — o atendimento volta a consumir créditos normalmente."** (best-effort, mesmo padrão).

## Integração com o worker (billing gate + débito)

`apps/worker/app/tasks/inbound_context.py`'s `InboundContext` ganha `end_customer_has_active_subscription: bool` (resolvido em `_load_context`, `apps/worker/app/tasks/messages.py`, via `SELECT` em `end_customer_subscriptions` filtrando `status="active"` e `current_period_end >= now()`).

- `maybe_enter_gate` (`apps/worker/app/billing_gate.py`) ganha `and not inbound.end_customer_has_active_subscription` na condição de entrada — assinante ativo nunca vê o gate de saldo esgotado.
- `process_inbound_message` (`apps/worker/app/tasks/messages.py`): quando `end_customer_has_active_subscription` é `True`, pula **tanto** `_debitar_creditos` (tenant) **quanto** `_debitar_creditos_cliente_final` (cliente final) — nenhum dos dois lados é cobrado por esse turno.

**Trade-off de negócio explícito** (decisão já tomada, documentar como tal): "ilimitado" significa ilimitado — não há teto automático de uso nem soft cap nesta v1. O tenant absorve o custo do LLM daquele cliente enquanto a assinatura estiver ativa; a expectativa é que o preço da assinatura já embuta margem suficiente pro volume esperado. Calibragem de preço fica a cargo de cada tenant.

## WhatsApp — lista de pacotes (billing gate)

`_packages_to_sections` (`apps/worker/app/billing_gate.py`) passa a renderizar **2 seções** na mensagem interativa `list` quando o tenant tem ao menos 1 pacote `kind="subscription"` ativo: "Pacotes de créditos" (linhas existentes, `kind="one_time"`) e "Assinatura mensal" (linhas novas, `kind="subscription"`) — cada linha descrevendo o tipo (ex: "R$ 49,90 = 500 créditos" vs "R$ 49,90/mês — conversas ilimitadas"). Quando o tenant não tem nenhum pacote de assinatura cadastrado, a lista continua com 1 seção só, comportamento idêntico ao atual.

## Painel (`/configuracoes/cobranca-clientes`)

- Formulário de pacote (`EndCustomerBillingPanel.tsx`, aba Configurações) ganha um seletor de tipo (Avulso/Assinatura mensal). Campo "Créditos" some do formulário quando "Assinatura mensal" está selecionado.
- Listagem de pacotes ganha um badge "Avulso"/"Mensal" por linha.
- Seletor de tipo só aparece quando `billing_provider === "connect"` — pra um tenant ainda `standalone`, o formulário continua igual a hoje (só "Avulso"), sem o seletor.

## Fora de escopo

- Suporte a assinatura recorrente para tenants em `billing_provider="standalone"` — ver "Decisão de escopo resultante" acima.
- Mais de uma assinatura ativa simultânea por cliente final.
- Upgrade/downgrade entre planos de assinatura, proration.
- Qualquer teto automático de uso pro assinante "ilimitado" (soft cap, alerta de consumo anômalo).
- Cancelamento self-service pelo cliente final via WhatsApp (o cancelamento, nesta v1, só acontece pelo próprio Dashboard Stripe do tenant ou por ação do tenant fora da plataforma — a Advoxs só reage ao evento `customer.subscription.deleted`, não oferece um fluxo de "cancelar assinatura" dentro do chat).
