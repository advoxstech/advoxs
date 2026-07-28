# Migração da cobrança do cliente final para Stripe Connect — Design

## Contexto

Hoje, a "cobrança do cliente final" (cada tenant cobrando os próprios clientes finais pelo uso dos agentes no WhatsApp — ver seção "Cobrança do cliente final" do `CLAUDE.md`) exige que o tenant tenha a própria conta Stripe e cole, manualmente, a secret key e o webhook secret dessa conta em `/configuracoes/cobranca-clientes`. Isso tem 3 problemas:

1. Exige que o tenant saiba operar o Dashboard da própria Stripe (criar chave restrita, criar endpoint de webhook, copiar secrets).
2. A plataforma nunca vê a conta do tenant — não há como oferecer onboarding guiado, status de configuração, ou qualquer camada de suporte.
3. Cada tenant é um webhook e uma chave separada — não escala operacionalmente pra plataforma dar suporte.

Esta spec descreve a migração para **Stripe Connect (Accounts v2)**, onde a plataforma Advoxs se torna a conta "dona" de todas as contas conectadas dos tenants, e o tenant configura tudo **dentro do próprio painel Advoxs**, sem nunca abrir o Dashboard da Stripe.

### Fora de escopo (não muda)

- **Billing tenant→Advoxs** (`/creditos`, `credit_packages`, `signup/checkout`, `/api/v1/webhooks/stripe`): é a fonte de receita da própria Advoxs, roda na conta Stripe da própria Advoxs (que é brasileira), e não tem nenhuma relação com Connect. Não é tocado por esta spec.
- **Modelo de créditos do cliente final** (`end_customer_credit_packages`, `end_customer_balances`, `end_customer_credit_transactions`, billing gate determinístico em `apps/worker/app/billing_gate.py`): a mecânica de saldo, pacotes e o funil "sem saldo → escolher pacote → pagar → liberado" não muda. Só muda **por onde o dinheiro do pagamento passa** (Connect em vez de conta standalone do tenant).
- **Plano de assinatura mensal recorrente pro cliente final** (feature desenhada em conversa anterior, ainda não aprovada/spec'ada): fica explicitamente parada até esta migração ser concluída — ver seção "Dependência cruzada" abaixo.

### Modelo de negócio (confirmado)

- A Advoxs **não cobra comissão** sobre a cobrança do cliente final — o valor integral (avulso ou, no futuro, recorrente) vai pro tenant.
- A receita da Advoxs continua vindo exclusivamente da venda de créditos da própria plataforma pro tenant (mecanismo já existente, fora de escopo aqui).
- Isso mapeia pro padrão **SaaS / Direct charges** da Stripe: a conta conectada (tenant) é a merchant of record, não a plataforma.

## Modelo de dados

`tenant_billing_settings` ganha 3 colunas novas:

| Coluna | Tipo | Descrição |
|---|---|---|
| `billing_provider` | `String`, default `"standalone"` | `"standalone"` (mecanismo atual, secret key colada) ou `"connect"` (Stripe Connect). Decide em runtime qual caminho checkout/webhook seguem. |
| `stripe_account_id` | `String`, nullable | `acct_...` da conta conectada v2. `NULL` até o tenant iniciar o onboarding Connect. |
| `stripe_account_status` | `String`, nullable | `"not_started"` \| `"onboarding"` \| `"active"` — espelha o status da capability `card_payments`/`pix` da conta conectada. `NULL` equivale a `"not_started"`. |

**Nenhuma outra tabela muda.** `end_customer_credit_packages`, `end_customer_balances`, `end_customer_credit_transactions`, `conversations.billing_gate_*` continuam idênticas.

`stripe_secret_key_encrypted`/`stripe_webhook_secret_encrypted` continuam existindo no schema (necessárias enquanto qualquer tenant real estiver em `billing_provider="standalone"`) — sem uso pra quem migrar pra `"connect"`.

**Decisão explícita: nenhum dado de CPF/CNPJ, endereço ou conta bancária do tenant é armazenado no Postgres da Advoxs.** Esses dados são coletados e guardados exclusivamente pela Stripe, dentro da conta conectada (via componente de onboarding embutido, seção abaixo). Se o painel precisar exibir algo relacionado (ex: nome cadastrado), busca-se em tempo de leitura na API da Stripe — nunca duplicado localmente.

## Onboarding (embutido — o tenant nunca sai da plataforma)

Em `/configuracoes/cobranca-clientes`, tenants com `billing_provider="connect"` veem um bloco de onboarding no lugar do formulário de secret key/webhook secret.

**Backend — `POST /api/v1/end-customer-billing/connect-account`** (autenticado, tenant-scoped):
1. Se `stripe_account_id` for `NULL`: cria a conta v2 (`POST /v2/core/accounts`) com:
   - `identity`: só `country="BR"` + e-mail de contato do tenant — **sem `entity_type`** (PF vs CNPJ é perguntado pela própria Stripe dentro do componente de onboarding, não pela Advoxs).
   - `dashboard: "full"`, `defaults.responsibilities.fees_collector: "stripe"`, `defaults.responsibilities.losses_collector: "stripe"` — padrão SaaS/Direct charges.
   - Capabilities solicitadas: `card_payments` + `pix` (Pix incluído desde a v1 desta feature, decisão confirmada — mercado brasileiro de cliente final usa Pix amplamente).
   - Salva o `acct_...` retornado em `stripe_account_id`, `stripe_account_status="onboarding"`.
2. Cria uma **Account Session** (`POST /v1/account_sessions`, `account={{stripe_account_id}}`, `components[account_onboarding][enabled]=true`).
3. Devolve `{client_secret}` pro frontend.

**Frontend** (`ConnectOnboardingPanel`, novo componente): usa **Connect.js** (`loadConnectAndInitialize`) pra montar o componente `account-onboarding` dentro de um container na própria página — formulário roda num iframe embutido no domínio da Advoxs, sem redirect externo. A própria Stripe pergunta PF vs CNPJ e monta os campos certos pra cada caso; captura o aceite de termos; valida os dados.

**`GET /api/v1/end-customer-billing/account-status`**: devolve `stripe_account_status` pro painel mostrar "onboarding em andamento" vs "ativo".

`enabled` em `tenant_billing_settings` só fica utilizável (billing gate passa a poder entrar) quando `stripe_account_status="active"`.

## Webhook — endpoint dedicado a Connect

**`POST /api/v1/webhooks/stripe/connect`** — endpoint novo, **separado** de `/webhooks/stripe/tenant/{tenant_id}` (que continua existindo, servindo só tenants em `standalone`). Connect exige um endpoint configurado uma única vez no Dashboard da própria Advoxs, escutando eventos de **todas as contas conectadas** — não um endpoint por tenant.

- **Um único segredo de assinatura** (`STRIPE_CONNECT_WEBHOOK_SECRET`, env da plataforma) valida todos os eventos, independente de qual tenant.
- Tenant resolvido via `event.account` (o `acct_...`) → lookup em `tenant_billing_settings.stripe_account_id`.
- Eventos tratados:
  - Evento de atualização de capability/status da conta → atualiza `stripe_account_status` (nome exato do evento v2 a confirmar durante a implementação — histórico é `account.updated`, mas a v2 pode ter um nome/formato diferente; ⚠️ item a verificar antes de codificar esse handler).
  - `checkout.session.completed` → mesma lógica de crédito de hoje (`process_end_customer_checkout_completed`), sem mudança de comportamento — só passa a rodar também para compras originadas de tenants `connect`.

## Checkout do cliente final

`create_end_customer_checkout_session` (`apps/api/app/services/end_customer_billing.py`) ramifica por `billing_provider`:
- `"standalone"` (comportamento atual, inalterado): `api_key=` explícito com a secret key do tenant.
- `"connect"`: monta a Checkout Session como **direct charge** referenciando a conta conectada do tenant — chave usada é a da própria plataforma, mas o dinheiro cai direto na conta do tenant (merchant of record). **Sem `application_fee_amount`** em nenhum dos dois casos.

⚠️ **Item a confirmar durante a implementação**: o parâmetro/mecanismo exato pra marcar uma Checkout Session como direct charge numa conta v2 (histórico: header `Stripe-Account` / parâmetro `stripe_account=` no SDK) precisa ser validado contra a API atual antes de codificar esta parte — não é um risco de arquitetura, é um detalhe de sintaxe a confirmar no momento da Task correspondente do plano de implementação.

## Rollout / migração

**Connect é o único modelo válido pra todos os tenants — novos e já existentes.** `billing_provider="standalone"` deixa de ser uma opção permanente; é só o estado transitório de quem ainda não migrou.

- **Tenants novos**: o fluxo de configuração de cobrança do cliente final oferece **só** o onboarding via Connect — a tela de colar secret key/webhook secret é removida do caminho de configuração inicial. Não é possível configurar `standalone` do zero depois desta entrega.
- **Tenants já em `billing_provider="standalone"`** (já com clientes finais reais pagando por aquele caminho): continuam funcionando durante uma janela de transição, mas a migração é **obrigatória, não opcional** — todo tenant com a cobrança do cliente final habilitada precisa concluir o onboarding Connect. Mecanismo de exigência (a decidir no plano de implementação): painel bloqueia edição/criação de novos pacotes de crédito do cliente final enquanto `billing_provider="standalone"`, e/ou um prazo-limite após o qual o checkout do cliente final para de funcionar em contas não migradas.
- **Migração de dados**: os tenants já em `standalone` recebem `billing_provider="standalone"` no backfill da migration (nenhum tenant migra automaticamente pra `connect` sem passar pelo onboarding real — não dá pra criar a conta conectada sem a ação do tenant preenchendo os dados na Stripe). O que muda é que **ficar em `standalone` vira um estado temporário monitorado**, não um destino permanente.
- **Depreciação do `standalone`**: assim que todos os tenants reais tiverem migrado, remoção das colunas `stripe_secret_key_encrypted`/`stripe_webhook_secret_encrypted`, do endpoint `/webhooks/stripe/tenant/{tenant_id}` e do formulário antigo — código morto até lá, fica pra uma spec de limpeza futura (mesmo padrão do "Gate único determinístico": rollout com prazo, remoção do mecanismo antigo só depois que ninguém mais depende dele).

## Dependência cruzada (não bloqueia esta spec)

O plano de assinatura mensal recorrente pro cliente final (desenhado em conversa anterior, ainda não aprovado) precisa, se implementado, rodar sobre configuração de **cliente v2 na própria conta conectada** (`customer_account`), nunca um `Customer` v1 separado — API de assinaturas com Connect v2 exige isso. Recomendação: sequenciar essa feature **depois** desta migração, em cima do modelo Connect já existente — implementá-la primeiro sobre o modelo `standalone` seria retrabalho.

## Riscos / itens a validar durante a implementação (não bloqueiam a spec)

- Nome exato do evento de status/capability em Accounts v2 (webhook).
- Sintaxe exata do parâmetro de direct charge na Checkout Session da API atual.
- Confirmar que a capability `pix` está disponível pra contas BR sob Direct charges (documentada como disponível para Connect em geral; validar durante o onboarding de teste).

## Fora de escopo desta entrega

- Depreciação/remoção do mecanismo `standalone` (fica para quando todos os tenants reais migrarem).
- Plano de assinatura mensal recorrente pro cliente final (spec própria, futura, dependente desta).
- Qualquer alteração no billing tenant→Advoxs.
