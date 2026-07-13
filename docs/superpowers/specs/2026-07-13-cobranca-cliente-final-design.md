# Design — Cobrança do Cliente Final (Stripe por tenant)

Data: 2026-07-13
Status: aprovado

## Objetivo

Hoje o billing existente é uma única camada: **tenant → plataforma**, em créditos, via a conta Stripe da própria plataforma. Esta entrega adiciona uma **segunda camada, independente**: **cliente final → tenant**, também em créditos, mas usando a conta Stripe de cada tenant (cada escritório cobra os próprios clientes pelo uso dos agentes).

As duas camadas não se misturam: o consumo de tokens de uma execução do agente continua debitando o crédito do *tenant* com a plataforma (como já acontece hoje) e, **adicionalmente**, quando essa feature está ativa pro tenant, debita também o crédito do *cliente final* com o tenant — duas contas, dois ledgers, mesma transação de banco.

## Decisões de produto

- **Modelo de cobrança**: pré-pago por créditos, espelhando exatamente o padrão que a plataforma já usa consigo mesma. O cliente final compra créditos do tenant (via Stripe do próprio tenant) e cada execução do agente consome desse saldo.
- **Opt-in por tenant** — toggle `enabled` em `tenant_billing_settings`, default `false`. Sem configurar, o agente continua respondendo de graça, comportamento idêntico ao atual, pra todo tenant existente.
- **Pacotes definidos por cada tenant** — cada escritório cadastra seus próprios pacotes (nome, preço em R$, créditos), não são pacotes fixos da plataforma.
- **Checkout gerado dinamicamente pela plataforma** — o tenant só cadastra a *secret key* da Stripe (nunca um link estático criado manualmente no Dashboard). A cada compra, o `api` cria uma Stripe Checkout Session na hora, com metadata identificando `tenant_id` + `contact_phone_number` + pacote — igual ao padrão já usado no billing tenant→plataforma (`create_checkout_session`/`create_recompra_checkout_session` em `app/services/billing.py`).
- **Oferta dos pacotes é conduzida pelo próprio agente de IA**, via tool — não é uma mensagem determinística fora do grafo. Mais flexível e conversacional; o custo de tokens dessa interação é do *tenant* com a plataforma (billing de hoje), independente do cliente final ter saldo ou não.
- **Bloqueio técnico no grafo** (não só orientação por prompt): o especialista (`agente_condominial`/`agente_contratos`/`agente_direito_consumidor`) só roda se o cliente final tiver saldo positivo. Sem saldo, a secretária nunca transfere — fica restrita a oferecer pacotes e gerar o link de pagamento.
- **Consumo por tokens, taxa configurável por tenant** — mesma fórmula de hoje (`ceil(tokens_used / N)`), mas `N` (tokens por crédito) é definido por tenant, não um valor global.
- **Arquitetura extensível**: a regra de acesso/consumo fica encapsulada numa interface própria no `api`, para que modos de cobrança futuros (assinatura, por conversa) possam ser adicionados sem alterar `worker`/`agents`. Só o modo `"credits"` é implementado nesta entrega.

## Modelo de dados (novas tabelas, todas tenant-scoped — RLS)

### `tenant_billing_settings` (1:1 com tenant)
- `id` (uuid, PK)
- `tenant_id` (FK → `tenants`, `UNIQUE`)
- `enabled` (bool, default `false`)
- `billing_mode` (texto, default `"credits"` — hook de extensibilidade, único valor suportado por ora)
- `stripe_secret_key_encrypted` (nullable — Fernet, nunca retornado em nenhuma resposta de API)
- `stripe_webhook_secret_encrypted` (nullable — Fernet, mesmo tratamento)
- `end_customer_tokens_per_credit` (inteiro, nullable até configurado)
- `created_at`, `updated_at`

### `end_customer_credit_packages` (tenant-scoped)
- `id` (uuid, PK)
- `tenant_id` (FK → `tenants`)
- `name`
- `price_brl` (numeric)
- `credits_granted` (integer)
- `active` (bool)
- `created_at`

### `end_customer_balances` (tenant-scoped)
- `id` (uuid, PK)
- `tenant_id` (FK → `tenants`)
- `contact_phone_number`
- `credit_balance` (integer, default `0`)
- `created_at`, `updated_at`
- `UNIQUE (tenant_id, contact_phone_number)`

### `end_customer_credit_transactions` (tenant-scoped, ledger)
- `id` (uuid, PK)
- `tenant_id` (FK → `tenants`)
- `contact_phone_number`
- `type` (`purchase` | `consumption`)
- `amount_credits` (integer — positivo em `purchase`, negativo em `consumption`)
- `end_customer_credit_package_id` (FK, nullable — preenchido em `purchase`)
- `related_message_id` (FK → `messages`, nullable — preenchido em `consumption`)
- `stripe_payment_id` (nullable, unique — idempotência, mesmo padrão de `credit_transactions`)
- `description`
- `created_at`

### Alteração em tabela existente
- `messages.sender_type` ganha um novo valor possível: **`system`** — usado exclusivamente pra mensagem de confirmação de pagamento enviada ao cliente final (não é resposta do agente nem de um humano do escritório).

## Painel do tenant (`web`)

Nova página **`/configuracoes/cobranca-clientes`** (mesmo padrão de `/configuracoes/whatsapp`):

- Toggle **"Cobrar meus clientes pelo uso dos agentes"** → liga/desliga `enabled`.
- Campo **Secret Key da Stripe** — write-only, nunca ecoa de volta (só indica "configurada", como o token do WhatsApp).
- Campo **Webhook Secret** — mesmo tratamento, com instrução na tela pro tenant criar um endpoint no próprio Dashboard Stripe apontando pra `https://<domínio>/api/v1/webhooks/stripe/tenant/{tenant_id}`, evento `checkout.session.completed` — o mesmo passo a passo que o `CLAUDE.md` já documenta pra plataforma configurar a própria conta, agora replicado pelo tenant na conta dele.
- Campo **conversão** (tokens por crédito) — numérico, com valor sugerido de partida.
- **CRUD de pacotes**: nome, preço em R$, créditos, ativo/inativo — lista + criar/editar/desativar.

### Rotas novas (`api`, tenant-scoped — `get_current_tenant` + `get_tenant_session`)
- `GET/PUT /api/v1/end-customer-billing/settings` — `PUT` aceita secret key/webhook secret opcionalmente (omitidos = mantém o valor já salvo); `GET` nunca retorna os valores em si, só booleanos `stripe_secret_key_configured`/`stripe_webhook_secret_configured`.
- `GET/POST/PATCH/DELETE /api/v1/end-customer-billing/packages`.

## Fluxo de cobrança dentro da conversa (WhatsApp)

### Contrato `POST /messages` (worker → agents) ganha um campo novo
```
end_customer_billing: {
  enabled: bool,
  balance: int,
  packages: [{id, name, price_brl, credits_granted}, ...]
}
```
O `worker` monta esse bloco **antes** de chamar o `agents` (busca `tenant_billing_settings` + `end_customer_balances` + pacotes ativos) — nenhuma chamada HTTP extra pra listar saldo/pacotes durante a conversa, os dados já chegam prontos no state do grafo. Quando `enabled=false`, o bloco é omitido/irrelevante e o comportamento é idêntico ao de hoje.

### Nova tool `gerar_link_pagamento_cliente(package_id)`
Bindada à secretária. Chama um **endpoint interno novo no `api`**: `POST /api/v1/internal/end-customer-billing/checkout`, autenticado por uma chave de serviço própria (`INTERNAL_SERVICE_KEY`, direção oposta da `AGENTS_API_KEY` que hoje protege a entrada do `agents`). O `api` decripta a secret key do tenant, cria a Checkout Session (`mode=payment`, `price_data` a partir do pacote, metadata `{tenant_id, contact_phone_number, package_id, kind: "end_customer_purchase"}`) e devolve `{checkout_url}` — a secret key nunca sai do `api`, o `agents` não ganha a dependência do SDK `stripe`.

Como o cliente final está no WhatsApp (não numa sessão logada do `web`), `success_url`/`cancel_url` apontam pra uma página pública e estática do `web` (ex: `/pagamento-confirmado`, sem polling nem dado sensível) — só uma tela de "pode fechar esta aba e voltar pro WhatsApp"; a confirmação de fato acontece assíncrona, via a mensagem de `system` que o webhook dispara (ver seção seguinte).

### Gate técnico no grafo
O nó/tool `transfer_to_specialist` passa a checar `state["end_customer_billing"]["balance"] > 0` antes de liberar a transferência — só quando `enabled=true` pra aquele tenant. Sem saldo, a transferência é negada e o retorno da tool instrui a secretária a oferecer os pacotes / gerar o link; o especialista nunca chega a rodar. Igual ao padrão já usado pelos `STATE_SCOPED_TOOLS` (o saldo vem do state injetado pelo `tool_node`, nunca é algo que o LLM possa forjar).

## Webhook por tenant + confirmação ao cliente final

**Nova rota**: `POST /api/v1/webhooks/stripe/tenant/{tenant_id}`. O `tenant_id` precisa estar na URL porque a verificação de assinatura da Stripe exige o secret correto já no momento de validar — não é possível "tentar" o secret de todos os tenants contra um payload.

Fluxo: busca `tenant_billing_settings` pelo `tenant_id` → decripta o webhook secret daquele tenant → `stripe.Webhook.construct_event` (assinatura inválida → `400`, igual ao webhook atual da plataforma) → em `checkout.session.completed` com `metadata.kind == "end_customer_purchase"`:
1. Idempotência por `stripe_payment_id` (mesma checagem já existente no billing atual).
2. Upsert em `end_customer_balances` (soma os créditos do pacote) + grava `end_customer_credit_transactions` (tipo `purchase`).
3. Envia uma mensagem via Graph API (usando o `access_token` do tenant, já decriptado no fluxo de conexão do WhatsApp) confirmando o pagamento e persiste em `messages` com `sender_type="system"`.

⚠️ Mesma pegadinha já conhecida do SDK: `event["data"]["object"]` é um `StripeObject` real, não dict — usar `.to_dict()` antes de ler a metadata com `.get()`.

## Consumo / débito

Depois da resposta do agente, o `worker` já converte `tokens_used` em créditos pra debitar do *tenant* junto à plataforma (fluxo atual, inalterado). **Na mesma transação**, se `enabled=true` **e** o saldo do cliente final era `> 0` antes da chamada ao `agents`, aplica `ceil(tokens_used / end_customer_tokens_per_credit)` em `end_customer_balances` + grava `end_customer_credit_transactions` (tipo `consumption`, `related_message_id`). Se o saldo já estava zerado, **não debita nada** — a interação foi só a secretária oferecendo pacotes, custeada normalmente pelo crédito do tenant com a plataforma.

## Extensibilidade

A regra de acesso/consumo fica encapsulada num módulo próprio no `api` (`app/services/end_customer_billing.py`), com uma interface pequena:
- `has_access(tenant, contact_phone_number) -> bool`
- `charge_usage(tenant, contact_phone_number, tokens_used) -> None`

Hoje só existe a implementação `"credits"` (lê `billing_mode` de `tenant_billing_settings`). `worker`/`agents` só chamam essa interface — não sabem (nem precisam saber) qual modo está ativo. Modos futuros (assinatura, por conversa) implementam a mesma interface sem alterar o restante do fluxo.

## Segurança

- `stripe_secret_key_encrypted`/`stripe_webhook_secret_encrypted`: Fernet, nova env própria `TENANT_STRIPE_KEY_ENCRYPTION_KEY` (separada da chave de encriptação do WhatsApp — blast radius isolado). Nunca retornados em nenhuma resposta de API.
- `INTERNAL_SERVICE_KEY` nova, protegendo o endpoint `agents → api` de criação de checkout (direção oposta da `AGENTS_API_KEY` existente, que protege a entrada do `agents`).
- Webhook por tenant: o `tenant_id` na URL é só roteamento pra achar o secret certo — sem assinatura Stripe válida (verificada com o secret daquele tenant específico), o request cai em `400`. Adivinhar um `tenant_id` não vaza nenhum dado.

## Erros e casos-limite

| Caso | Comportamento |
|---|---|
| Tenant com `enabled=true` mas sem secret key configurada | `gerar_link_pagamento_cliente` retorna erro tratável pela tool (a secretária informa que o pagamento está temporariamente indisponível); não derruba a conversa |
| Falha ao criar Checkout Session na Stripe (rede/erro da API) | Endpoint interno retorna erro; a tool repassa uma mensagem de erro genérica pro agente comunicar ao cliente |
| Webhook duplicado (retry da Stripe) | Idempotente por `stripe_payment_id`, mesma checagem já existente |
| Cliente final paga mas a conversa nunca mais volta a interagir | Saldo fica creditado em `end_customer_balances`, consumido normalmente na próxima interação — sem expiração nesta entrega |
| `enabled=false` (feature desligada) | Nenhuma checagem de saldo ocorre; comportamento idêntico ao atual |

## Testes

- **`api`**: unit — `end_customer_billing.py` (`has_access`/`charge_usage`), webhook por tenant (assinatura válida/inválida, idempotência, metadata incompleta), rotas de settings/pacotes (RLS, write-only da secret key), endpoint interno de checkout (auth por `INTERNAL_SERVICE_KEY`).
- **`agents`**: unit — tool `gerar_link_pagamento_cliente` (mock HTTP pro endpoint interno), gate técnico em `transfer_to_specialist` (saldo `0` bloqueia, saldo `>0` libera, `enabled=false` não muda nada).
- **`worker`**: unit — débito duplo (tenant + cliente final) na mesma transação; saldo zerado não debita nada do cliente final.
- **`web`**: componente da nova página `/configuracoes/cobranca-clientes` (mock da API).

## Fora de escopo desta entrega

- Migração de dados/collections antigas — não aplicável (feature nova).
- Suporte a Stripe Connect / onboarding OAuth — decisão explícita de manter secret key colada manualmente por ora; pode ser revisitado no futuro se o volume de tenants justificar.
- Expiração de saldo do cliente final.
- Reembolso/estorno de créditos do cliente final.
- Múltiplos modos de cobrança simultâneos (a interface é extensível, mas só `"credits"` é implementado agora).
