# Login automático pós-pagamento

**Data:** 2026-07-16
**Status:** aprovado

## Problema

Depois de pagar o cadastro self-service, o usuário cai em `/cadastro/sucesso`, espera o polling
confirmar a conta e ainda precisa digitar e-mail/senha em `/login`. Atrito desnecessário no
momento mais sensível do funil — a pessoa acabou de pagar.

Restrição de segurança central: a conta nasce assíncrona (webhook `checkout.session.completed`),
e o retorno da Stripe carrega o `session_id` na URL. URLs vazam (histórico, logs, referrer) —
o `session_id` **nunca** pode virar credencial de login por si só.

## Solução

Token one-time de auto-login, com entrega única via polling e troca única por JWT.

### 1. Geração (webhook)

Em `_process_signup` (`apps/api/app/services/billing.py`), logo após criar
tenant+user+créditos com sucesso: gera `token = secrets.token_urlsafe(32)` e grava **duas
chaves no Redis** (o `api` já usa Redis pra blacklist de refresh), ambas com **TTL 900s**:

- `signup:handoff:{session_id}` → token em claro (entrega única via status).
- `signup:token:{sha256(token) hex}` → `user_id` (troca única por JWT; em repouso só o hash).

- Webhook duplicado da Stripe já retorna cedo (`already_processed`) — token gerado exatamente
  uma vez por cadastro.
- Falha na escrita do Redis: loga warning e segue — a conta está criada; o auto-login degrada
  pro fluxo atual (link pro `/login`). Nunca falha o webhook por causa disso.
- O fluxo de **recompra** (`_process_recompra`) NÃO gera token (usuário já está logado).

### 2. Entrega única (`GET /signup/status`)

Resposta passa de `{ready}` para `{ready, login_token: str | null}`: quando `ready=true`,
faz **`GETDEL`** em `signup:handoff:{session_id}` e devolve o valor (ou `null` se já
entregue/expirado/conta antiga). O primeiro a perguntar leva — o navegador legítimo está
pollando a cada 2s desde antes de a conta existir, então sempre ganha; a URL vazada depois
não destrava mais nada.

### 3. Troca única (`POST /api/v1/auth/signup-login` — novo, público)

Body `{token: str}` → `GETDEL` em `signup:token:{sha256(token)}` (uso único) → carrega o
`User` (e o tenant; tenant suspenso → 403 como no login) → emite o **mesmo par**
access+refresh do login normal. Token inválido/expirado/já usado → **401 genérico**
("Token inválido ou expirado"), sem distinguir os casos (sem oráculo).

- Fica no router de `auth` (`apps/api/app/api/v1/auth.py`), sessão `get_system_session`
  (rota pública, lookup de user por id — mesmo padrão do login por e-mail).

### 4. Web

- **Server action nova** (`apps/web/src/app/cadastro/actions.ts`): `autoLogin(token)` — POST
  `${API_URL}/api/v1/auth/signup-login`, `setAuthCookies` (mesmo helper do login),
  `redirect("/inicio")`. Falha → retorna erro; o painel cai no fallback.
- **`SignupSuccessPanel`**: quando o status vier com `login_token`, mostra "Entrando…" e chama
  a action. Sem `login_token` (ou action falhou): comportamento atual preservado — botão
  "Ir para o login".
- `/creditos/sucesso` (recompra) não muda.

## Testes

- **api** (unit): webhook grava as 2 chaves com TTL (mock de Redis) e não regrava em webhook
  duplicado; recompra não grava; status com `ready=true` devolve o token na primeira chamada e
  `null` na segunda (GETDEL); status `ready=false` não toca no Redis; `signup-login` — token
  válido → par de tokens (e a chave some), token inválido → 401, reuso → 401, tenant suspenso
  → 403.
- **web** (unit): painel com `login_token` chama a action com o token; sem `login_token`
  mantém o botão "Ir para o login"; erro da action cai no fallback com o botão.

## Documentação

- CLAUDE.md: seção Billing/Créditos (fluxo do cadastro ganha o auto-login) e Frontend/`/`
  (`/cadastro/sucesso` loga sozinho).

## Fora de escopo

- Auto-login na recompra (`/creditos/sucesso`).
- Verificação de e-mail no cadastro (continua fora, como antes).
- Rate limiting dedicado no endpoint novo (o 401 genérico + uso único + TTL curto já limitam
  o valor de brute force; token de 32 bytes é inviável de adivinhar).
