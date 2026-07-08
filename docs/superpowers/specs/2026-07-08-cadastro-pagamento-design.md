# Design — Cadastro self-service com pagamento (Stripe)

Data: 2026-07-08
Status: aprovado

## Objetivo

Permitir que um novo escritório se cadastre na plataforma sem intervenção manual: escolhe um pacote de créditos na página inicial, paga via Stripe, e um novo `tenant` + `user` são provisionados automaticamente. Login normal (já implementado) continua servindo quem já tem conta.

## Decisões de produto

- **Modelo de cobrança: pré-pago, compra pontual** — sem assinatura recorrente. Mantém o schema atual (`credit_packages`/`credit_transactions`) sem nenhuma mudança de tabela; usa Stripe Checkout em modo `payment`, não `subscription`.
- **Tenant/usuário só são criados após o pagamento confirmado** (via webhook) — nada persiste no banco a partir de uma tentativa de cadastro abandonada no meio do checkout.
- **CNPJ opcional no cadastro** — o campo já existe em `Tenant` (nullable, unique); pode ser preenchido depois.
- **Sem verificação de e-mail** — quem pagou já prova posse de um e-mail válido (recibo da Stripe); a conta fica ativa pra login imediatamente após o webhook confirmar. Evita depender de um provedor de e-mail (SES/Resend/etc.), que não existe no projeto hoje.
- **Pacotes reais**: os 4 valores já documentados no CLAUDE.md (Starter R$100/1.000cr, Growth R$250/2.750cr+10%, Scale R$500/6.000cr+20%, Enterprise R$1.000/13.000cr+30%) — entram via migration de dados, editáveis depois sem precisar de deploy.
- **Página inicial (`/`) é a própria página de planos + formulário** — sem uma landing de marketing separada. Quem não tem sessão vê os planos e o formulário direto em `/`; quem já tem sessão continua sendo redirecionado pra `/conversas` (comportamento atual do middleware preservado nesse caso).

## Decisão de arquitetura: Stripe Checkout hospedado + dados pendentes na metadata da sessão

**Abordagem escolhida**: o formulário coleta os dados (nome do escritório, e-mail, senha, pacote); o `api` valida, faz hash da senha, e cria uma Stripe Checkout Session (modo `payment`) com esses dados guardados na `metadata` da sessão — nada é persistido no nosso banco ainda. O navegador é redirecionado pro checkout 100% hospedado pela Stripe (formulário de cartão do lado deles — zero responsabilidade de PCI compliance nossa). Ao confirmar o pagamento, o webhook (`checkout.session.completed`) lê a `metadata` e cria `tenant` + `user` + `credit_transactions` numa única transação.

Alternativas descartadas:
- **Stripe Elements** (formulário de cartão embutido na nossa página) — mais controle visual, mas exige lidar com SCA/3D Secure e responsabilidade de PCI compliance; desproporcional ao que foi pedido.
- **Tabela de "cadastros pendentes"** no nosso banco em vez de usar a `metadata` da Stripe — evitaria depender da Stripe pra guardar dado temporário, mas adiciona uma tabela nova + rotina de limpeza de registros nunca pagos, sem ganho real dado que a decisão já é "nada persiste antes do pagamento" (a Stripe já garante isso de graça via metadata).

**Chave de idempotência**: o `id` da Checkout Session (`cs_...`) é usado consistentemente do início ao fim — é o mesmo valor usado no `success_url` (placeholder `{CHECKOUT_SESSION_ID}` da própria Stripe), gravado como `credit_transactions.stripe_payment_id` pelo webhook, e consultado pelo endpoint de status. Evita depender também do `payment_intent` id.

## Endpoints (`api`)

Novos, públicos (sem `get_current_tenant`/`get_tenant_session`):

### `GET /api/v1/credit-packages`

Lista os pacotes com `active=true` (`id`, `name`, `price_brl`, `credits_granted`) — usado para montar os cards de plano na página inicial.

### `POST /api/v1/signup/checkout`

Body: `{tenant_name, email, password, credit_package_id}`.

1. Valida: `email` ainda não existe em `users` (`409` se já existir — checagem *antes* de criar a sessão Stripe, pra não gastar um checkout com um cadastro que vai falhar depois de qualquer forma); `password` com no mínimo 8 caracteres (`422` via Pydantic); `credit_package_id` existe e está `active` (`400` senão).
2. Faz hash da senha (mesma função de hash já usada no resto do projeto).
3. Cria a Checkout Session na Stripe: modo `payment`, `line_items` com o preço do pacote, `metadata = {tenant_name, email, password_hash, credit_package_id}`, `success_url` = `{WEB_APP_URL}/cadastro/sucesso?session_id={CHECKOUT_SESSION_ID}`, `cancel_url` = `{WEB_APP_URL}/cadastro/cancelado`.
4. Retorna `{checkout_url}`. Falha de rede/API ao criar a sessão → `502`, mensagem genérica em pt-BR, log do erro real (mesmo padrão do client do WhatsApp).

### `POST /api/v1/webhooks/stripe`

Recebe o evento, valida a assinatura via `STRIPE_WEBHOOK_SECRET` (mesmo princípio do `X-Hub-Signature-256` do webhook do WhatsApp) — assinatura inválida → `400`, nada processado.

No evento `checkout.session.completed`:
1. Verifica idempotência: já existe `credit_transactions.stripe_payment_id` igual ao `id` da sessão? Se sim, `200` sem fazer nada (a Stripe reenvia webhooks em caso de falha/timeout do nosso lado).
2. Senão, lê a `metadata` e, numa única transação: cria `Tenant` (`name=tenant_name`, `email_contato=email`, `status=active`), cria `User` (`tenant_id`, `email`, `password_hash` da metadata, `role=admin`), lança `CreditTransaction` (`type=purchase`, `amount_credits` = `credits_granted` do pacote, `credit_package_id`, `stripe_payment_id` = id da sessão), atualiza `tenants.credit_balance`.
3. Outros tipos de evento são ignorados com `200` (não processamos nada além de `checkout.session.completed` nesta entrega).

### `GET /api/v1/signup/status?session_id=...`

Consulta `credit_transactions` por `stripe_payment_id = session_id`. Retorna `{ready: true}` se encontrado, `{ready: false}` senão. Endpoint público — não expõe nenhum dado do tenant, só o booleano.

## Segurança

- Senha nunca sai do backend em texto puro — só o hash bcrypt vai pra `metadata` da Stripe (mesma exposição de já estar gravado no nosso banco).
- Assinatura do webhook validada obrigatoriamente — sem isso, um request forjado criaria tenant de graça.
- Idempotência pelo `stripe_payment_id` evita duplicar tenant/crédito em retry de webhook.
- `GET /signup/status` não devolve nenhum dado sensível.
- Sem rate-limiting no `POST /signup/checkout` nesta entrega (nenhum outro endpoint do projeto tem hoje) — registrado como pendência de hardening futuro.

## Frontend (`web`)

### `/` (pública)

`middleware.ts`: o branch de `pathname === "/"` muda — sem sessão, deixa renderizar a página (não redireciona mais pra `/login`); com sessão, continua redirecionando pra `/conversas` (comportamento preservado). A página busca `GET credit-packages` e renderiza os 4 cards (nome, preço, créditos) + formulário (nome do escritório, e-mail, senha) com o plano selecionado. Link "Já tem conta? Entrar" pra `/login`. Submit → `POST signup/checkout` → `window.location.href = checkout_url`.

### `/cadastro/sucesso`

Lê `session_id` da query string. Faz polling em `GET signup/status` a cada 2s, até 8 tentativas (~16s), mostrando "Confirmando seu pagamento..." enquanto `ready=false`. Quando `ready=true`: "Conta criada! Você já pode entrar." + botão pra `/login`. Se esgotar as tentativas sem confirmar: mesma mensagem de sucesso com tom neutro ("Pagamento em processamento — tente entrar em instantes.") + o mesmo botão, nunca uma mensagem de erro (o pagamento já foi aprovado pela Stripe nesse ponto, só o nosso webhook pode estar atrasado).

### `/cadastro/cancelado`

"Pagamento cancelado — nenhuma cobrança foi feita." + botão de volta pra `/`.

Nenhuma dessas 3 rotas entra no `matcher` do middleware — ficam públicas por padrão, mesmo princípio que já vale pra `/login` hoje (rotas fora do `matcher` nunca são interceptadas).

## Dados de referência (seed)

Os 4 pacotes entram via **migration Alembic de dados** (INSERT, não script manual) — é dado de referência que precisa existir de forma idêntica em qualquer ambiente (dev/staging/produção), mesmo princípio de qualquer outra migration do projeto.

## Erros e casos-limite

| Caso | Comportamento |
|---|---|
| E-mail já cadastrado | `409` antes de criar a sessão Stripe |
| Pacote inexistente/inativo | `400` |
| Senha curta (< 8 caracteres) | `422` (Pydantic) |
| Falha ao criar sessão na Stripe (rede/API) | `502`, mensagem genérica, log do erro real |
| Webhook com assinatura inválida | `400`, rejeitado sem processar |
| Webhook duplicado (mesmo `session_id` já processado) | `200`, no-op |
| Pagamento cancelado pelo usuário | Stripe redireciona pra `/cadastro/cancelado`, nada foi criado |
| `signup/status` com `session_id` desconhecido/ainda não processado | `{ready: false}`, `200` |
| Evento de webhook diferente de `checkout.session.completed` | `200`, ignorado |

## Testes

- **api** (`tests/unit`): `credit-packages` lista só os `active=true`; `signup/checkout` feliz (sessão Stripe mockada, `metadata` com os campos corretos), e-mail duplicado → `409`, pacote inválido/inativo → `400`, senha curta → `422`; webhook feliz (cria `tenant`+`user`+`credit_transaction` com os valores corretos numa transação), assinatura inválida → `400`, evento duplicado (mesmo `session_id`) → `200` sem duplicar linhas, evento de tipo diferente → `200` ignorado; `signup/status` ready/not-ready.
- **web** (Vitest): página inicial renderiza os cards a partir do mock do fetch de `credit-packages`; submit do formulário chama `signup/checkout` e aciona o redirect com a `checkout_url` retornada; `/cadastro/sucesso` cobre os 3 estados (aguardando, pronto, timeout sem erro); `/cadastro/cancelado` renderiza o conteúdo estático.

## Fora de escopo desta entrega

- Assinatura recorrente / renovação automática de créditos.
- Verificação de e-mail (clique em link de confirmação).
- Rate-limiting no endpoint público de checkout.
- Upgrade/downgrade de plano, cupons de desconto, faturamento/nota fiscal.
- Landing page de marketing separada da página de cadastro (planos + formulário ficam direto em `/`).
- Reenvio de recibo/nota, painel de histórico de compras para o próprio tenant (além do que já existe implicitamente no ledger `credit_transactions`).
