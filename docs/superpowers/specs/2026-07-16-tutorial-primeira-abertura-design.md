# Tutorial de primeira abertura (configurações iniciais)

**Data:** 2026-07-16
**Status:** aprovado

## Problema

Uma conta recém-criada cai num painel vazio sem orientação: o dono do escritório não sabe que
precisa configurar o WhatsApp Business (app na Meta, token, webhook) nem que existe a cobrança
de clientes via Stripe própria — e não há caminho óbvio pra "experimentar antes de configurar".

## Solução

Wizard de boas-vindas em página dedicada, mostrado uma única vez por tenant, skippável, com
cada passo explicando e apontando pras páginas reais de configuração (sem duplicar forms).

### 1. Flag e backfill

- Migration `0012`: coluna `tenants.onboarding_completed_at` (timestamptz, nullable) **com
  backfill** no próprio `upgrade()` — `UPDATE tenants SET onboarding_completed_at = now()`
  pra todas as linhas existentes. Só contas criadas depois do deploy veem o tutorial.
- `downgrade()` dropa a coluna.
- Espelhar a coluna em `apps/worker/app/tables.py` NÃO é necessário (o worker não a lê) —
  registrar a decisão pra não confundir com o padrão das features anteriores.

### 2. API — router novo `apps/api/app/api/v1/onboarding.py` (tenant-scoped)

- `GET /api/v1/onboarding` → `{"completed": bool}` (`onboarding_completed_at IS NOT NULL`).
- `POST /api/v1/onboarding/complete` → 204. Seta `now()` se NULL; idempotente (re-POST não
  muda o timestamp original nem erra).
- Ambas com `get_current_tenant` + `get_tenant_session`.

### 3. Página `/boas-vindas` (web — wizard client, 3 passos)

- **Passo 1 — Boas-vindas**: o que a plataforma faz (agentes prontos pro escritório,
  atendimento via WhatsApp, modelo de créditos). Botão "Começar" → passo 2.
- **Passo 2 — WhatsApp Business**: explica o setup do lado da Meta (criar/acessar app,
  System User com token permanente, adicionar e verificar o número, PIN de 2 fatores,
  configurar o webhook) e exibe **callback URL + verify token copiáveis** reusando
  `GET /api/v1/whatsapp/webhook-config` (mesmo padrão de fetch/copiar do
  `WhatsAppConnectionPanel`; se o fetch falhar, os campos não aparecem e o texto continua).
  Botões: "Configurar WhatsApp agora" (completa + navega pra `/configuracoes/whatsapp`) e
  "Próximo" → passo 3.
- **Passo 3 — Cobrança de clientes (opcional)**: explica o opt-in da cobrança do cliente
  final (conta Stripe própria do escritório, secret key + webhook secret, pacotes de crédito
  próprios). Botões: "Configurar cobrança" (completa + navega pra
  `/configuracoes/cobranca-clientes`) e "Concluir" (completa + navega pra `/inicio`).
- **Rodapé fixo em todos os passos**: "Pular e testar os agentes" — completa + navega pra
  `/conversas?aba=testes`.
- Indicador de progresso (passo 1 de 3 etc.), mesma linguagem visual do painel (tokens de
  `globals.css`). Rota protegida: adicionar `/boas-vindas` ao matcher do middleware.
- "Completar" = `POST /api/v1/onboarding/complete` disparado em QUALQUER saída do wizard
  (concluir, configurar agora, pular) antes de navegar — o tutorial nunca reaparece. Sem
  botão "rever tutorial" (YAGNI). Se o POST falhar, navega mesmo assim (pior caso: o wizard
  reaparece no próximo login).

### 4. Redirecionamento (gate no `/inicio`)

- Componente client novo `OnboardingGate` envolvendo o conteúdo de `/inicio`
  (`apps/web/src/app/inicio/page.tsx`): busca `GET /api/v1/onboarding` via `backendFetch`;
  `completed=false` → `router.replace("/boas-vindas")`; `completed=true` → renderiza os
  children. Enquanto checa, mostra o loading padrão ("Carregando...") — sem flash do
  dashboard.
- **Fail-open**: se a checagem falhar (rede/5xx), renderiza o dashboard normalmente — o
  tutorial é nice-to-have e nunca pode trancar o painel.
- Login e auto-login continuam apontando pra `/inicio`; o gate decide.

### 5. Aba Testes via URL

- `/conversas` aceita `?aba=testes`: a page (`apps/web/src/app/conversas/page.tsx`) lê o
  `searchParams` e passa `initialOrigin="test"` pro `ConversationsPanel`, que ganha a prop
  opcional `initialOrigin?: "real" | "test"` (default `"real"`) usada só como estado inicial
  da aba — nenhuma outra mudança no painel.

## Testes

- **api** (unit): `GET` reflete NULL/preenchido; `POST` seta e é idempotente (segundo POST
  não altera o timestamp); 401 sem token.
- **web** (unit): `OnboardingGate` — redireciona quando `completed=false`, renderiza children
  quando `true`, fail-open em erro de rede; wizard — navegação entre passos, cada saída chama
  o POST e navega pro destino certo, "Pular" disponível em todos os passos, campos de webhook
  aparecem quando o config responde; `ConversationsPanel` com `initialOrigin="test"` abre na
  aba Testes (e default continua `real`).

## Documentação

- CLAUDE.md: seção Frontend ganha o bullet de `/boas-vindas` (+ nota do gate em `/inicio` e
  do `?aba=testes` em `/conversas`); "Estado atual do repositório" ganha as rotas de
  onboarding.

## Fora de escopo

- "Rever tutorial" depois de completado.
- Progresso por passo persistido (o flag é único, tudo-ou-nada).
- Tutorial pro painel de admin da plataforma.
- Checklist de setup no dashboard (ex: "2 de 3 configurados") — evolução futura.
