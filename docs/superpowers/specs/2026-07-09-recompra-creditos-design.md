# Design — Recompra de Créditos (`/creditos`)

Data: 2026-07-09
Status: aprovado

## Objetivo

Tela para escritórios **já cadastrados** comprarem mais créditos, sem precisar do fluxo de cadastro. A integração com a Stripe já existe (Checkout Sessions, `price_data` dinâmico a partir de `credit_packages`, webhook de confirmação) — usada hoje só no cadastro self-service (que cria um tenant novo). Esta entrega reaproveita a mesma infraestrutura pra um tenant já existente, autenticado, comprar mais crédito e ver o saldo atualizado.

## Decisões de produto

- **Sem histórico de transações nesta entrega** — a tela mostra só o saldo atual e os 4 pacotes disponíveis. Ver `credit_transactions` fica como evolução futura (o painel de admin já tem esse padrão em `/admin/tenants/[id]`, reaproveitável depois).
- **Sem bloqueio de compra por status do tenant** — hoje não existe nenhuma ação que suspenda um tenant de verdade (o campo `status` existe no modelo, mas o painel de admin é somente leitura); não há regra a aplicar ainda.
- **Sem mudança na regra de saldo zerado/negativo** — pendência já registrada no CLAUDE.md, não faz parte desta entrega.

## Decisão de segurança: `tenant_id` nunca vem do cliente

A recompra é estruturalmente diferente do cadastro self-service: lá, o pagamento **cria** um tenant novo (não existe ainda, então a metadata carrega os dados de cadastro). Aqui, o tenant **já existe** e está autenticado — o pagamento só credita o saldo dele.

Isso exige uma garantia explícita: o `tenant_id` usado no webhook para creditar o saldo **nunca pode vir do corpo de uma requisição do cliente** — precisa vir sempre do contexto autenticado (`get_current_tenant`, resolvido a partir do JWT) no momento de **criar** a sessão de checkout, e ser gravado na metadata da sessão pelo próprio servidor. Como só o `api` escreve essa metadata (a Stripe não permite alteração posterior pelo cliente), ela é segura de usar de volta no webhook. Sem essa garantia, um tenant malicioso poderia manipular um `tenant_id` arbitrário e creditar saldo na conta de outro escritório.

## Rotas novas (`api`)

Todas autenticadas com `get_current_tenant` (mesma dependency usada em `/conversations`, `/knowledge-base/files` etc.):

- **`GET /api/v1/billing/balance`** → `{credit_balance: int}` — saldo atual do tenant autenticado (`tenants.credit_balance`).
- **`POST /api/v1/billing/checkout`** — body `{credit_package_id: uuid}` (**sem** `tenant_id`, que vem do JWT). Valida o pacote (existe e `active=true`), cria uma Stripe Checkout Session (`mode=payment`, `price_data` dinâmico a partir do pacote, mesmo padrão do cadastro), com metadata `{flow: "recompra", tenant_id: <do JWT>, credit_package_id}`. `success_url=/creditos?session_id={CHECKOUT_SESSION_ID}`, `cancel_url=/creditos`. Retorna `{checkout_url}`.
- **`GET /api/v1/billing/status?session_id=...`** → `{ready: bool}` — mesma lógica do `signup/status`: `ready=true` quando existe uma `credit_transactions` com esse `stripe_payment_id`.

## Webhook — ramificação por `flow`

`POST /api/v1/webhooks/stripe` já trata `checkout.session.completed` via `process_checkout_completed`, que hoje sempre cria `tenant`+`user`+`credit_transaction` (fluxo de cadastro). Passa a ramificar pelo campo `flow` da metadata:

- Metadata sem `flow` ou `flow="signup"` (comportamento atual, senão quebra o cadastro self-service já em produção) → cria tenant+user+transação, como hoje.
- Metadata com `flow="recompra"` → busca o `Tenant` existente pelo `tenant_id` da metadata, lança uma `credit_transactions` (tipo `purchase`, `amount_credits` do pacote, `stripe_payment_id`) e soma em `tenants.credit_balance` — **sem** criar `User`/`Tenant` novos.

A checagem de idempotência (já existente, por `stripe_payment_id` único em `credit_transactions`) continua valendo pros dois fluxos, sem duplicação de lógica.

⚠️ **Pegadinha já conhecida do SDK**: `event["data"]["object"]` é um `StripeObject` real (não dict) — usar `.to_dict()` antes de ler a metadata com `.get()`, como já corrigido no fluxo de signup.

## Front (`web`)

- **Página `/creditos`**: mostra o saldo atual (via `GET billing/balance`) e os 4 pacotes (via `GET credit-packages`, rota pública já existente), cada um como um card com botão **"Comprar"** — ao clicar, chama `POST billing/checkout` e redireciona pro `checkout_url` da Stripe. Sem formulário de cadastro (diferente do `SignupForm`, que coleta nome/e-mail/senha — aqui o tenant já existe).
- **Retorno do checkout**: ao voltar com `?session_id=...`, faz polling curto em `GET billing/status` (mesmo padrão do `SignupSuccessPanel` — poucas tentativas, intervalo curto) até `ready=true`, então recarrega o saldo e mostra confirmação. Timeout não é um erro fatal — o saldo aparece atualizado na próxima visita à página de qualquer forma.
- **Proxy autenticado**: allowlist do `/api/backend/*` (`isAllowedPath` em `lib/backend.ts`) ganha o prefixo `"billing"`.
- **Nav do tenant**: hoje `/conversas`, `/base-de-conhecimento` e `/configuracoes/whatsapp` duplicam o mesmo bloco de `<nav>` manualmente (mesmo padrão que existia no painel de admin antes do `AdminNav`). Como esta entrega adiciona um 4º item ("Créditos"), o bloco é extraído para um `TenantNav` compartilhado — mesma lógica do `AdminNav` (item ativo vira `<span>`, os demais `<Link>`), sem mudança visual nas 3 páginas existentes.

## Erros e casos-limite

| Caso | Comportamento |
|---|---|
| `credit_package_id` inexistente ou inativo | `400` |
| Falha ao criar sessão na Stripe (rede/erro da API) | `502` |
| `session_id` de outro tenant consultado em `GET billing/status` | Retorna `ready` normalmente — a checagem é só por `stripe_payment_id`, não expõe dado sensível (mesmo comportamento do `signup/status`, que já é público) |
| Webhook duplicado (retry da Stripe) | Idempotente — não duplica crédito, mesma checagem já existente |
| Sessão de recompra completada mas tenant foi apagado nesse intervalo (cenário teórico, sem exclusão de tenant implementada) | Logado como erro, webhook não falha (mesmo padrão defensivo do fluxo de signup para pacote/metadata ausente) |

## Testes

- **api**: `create_checkout_session` de recompra (metadata correta, sem dados de cadastro); `process_checkout_completed` ramificando por `flow` (recompra credita tenant existente sem criar user; signup continua criando tenant, sem regressão); `GET billing/balance` e `billing/status` autenticados (401 sem token); idempotência do webhook de recompra (reenvio não duplica crédito).
- **web**: `TenantNav` (item ativo não é link, os demais são — mesmo teste do `AdminNav`); página `/creditos` renderiza saldo + pacotes; clique em "Comprar" chama o checkout e redireciona; polling pós-checkout atualiza o saldo exibido.

## Fora de escopo desta entrega

- Histórico de transações na tela do escritório.
- Bloqueio de compra por status do tenant (`suspended`).
- Qualquer mudança na regra de comportamento quando o saldo zera/negativa.
