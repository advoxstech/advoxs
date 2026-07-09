# Design — `/inicio` (dashboard do escritório)

Data: 2026-07-09
Status: aprovado

## Objetivo

Página inicial pós-login do escritório (tenant): visão geral do estado da conta — saldo, WhatsApp, conversas, consumo e base de conhecimento — com atalhos pras áreas do painel. Substitui o antigo placeholder `/rom` do CLAUDE.md (renomeado para `/inicio`, consistente com as demais rotas em pt-BR) e passa a ser o destino pós-login no lugar de `/conversas`.

## Decisões de produto

- **Nome da rota: `/inicio`** (decidido com o usuário; o CLAUDE.md deixa de mencionar `/rom`).
- **Vira a página inicial pós-login**: o `redirect` da server action de login e os 2 redirects do middleware (`/` com sessão, `/login` com sessão) passam a apontar pra `/inicio`. `/conversas` continua acessível pela nav.
- **Stat tiles + uma lista, sem gráficos** — as métricas são magnitudes isoladas (mesmo racional do dashboard do admin); série temporal/gráfico fica como evolução futura se houver demanda.
- **Sem polling** — os dados carregam uma vez no mount; recarregar a página atualiza.

## Dados — endpoint agregado novo

**`GET /api/v1/dashboard`**, autenticado com `get_current_tenant` + sessão via `get_tenant_session` (filtro explícito por `tenant_id` em toda query + RLS, padrão de defesa em profundidade do repo).

Justificativa de não reaproveitar os endpoints existentes no client: os endpoints de lista (`/conversations`, `/knowledge-base/files`) devolvem payloads inteiros quando o dashboard só precisa de contagens, e as métricas de consumo (créditos gastos, mensagens do agente nos últimos 30 dias) não existem em nenhum endpoint de tenant hoje. Um endpoint único espelha o padrão já validado do `build_dashboard` do admin, escopado por tenant.

Payload (`TenantDashboardOut`):

| Campo | Fonte |
|---|---|
| `credit_balance` | `tenants.credit_balance` do tenant autenticado |
| `whatsapp` | `{connected: bool, display_phone_number: str \| null}` — de `whatsapp_numbers` (`status='connected'`); número mascarado no mesmo formato de `GET /whatsapp/connection` |
| `conversations` | `{total: int, waiting_human: int}` — `COUNT(*)` e `COUNT(*) WHERE state='human'` em `conversations` do tenant |
| `usage_last_30_days` | `{agent_messages: int, credits_consumed: int}` — `COUNT(*)` de `messages` com `sender_type='agent'` e soma de `credit_transactions` tipo `consumption` (valor absoluto), ambos filtrados aos últimos 30 dias |
| `knowledge_base` | `{ready: int, error: int}` — contagens por `status` em `knowledge_base_files` |
| `recent_conversations` | últimas 5 por `last_message_at` desc: `{id, contact_phone_number, state, last_message_at}` |

Service em `app/services/dashboard.py` (`build_tenant_dashboard(session, tenant_id)`), rota em `app/api/v1/dashboard.py` — mesmo desenho do par admin (`admin_dashboard.py`), mas tenant-scoped.

## Frontend (`web`)

- **Página `/inicio`** (`apps/web/src/app/inicio/page.tsx`): `TenantNav active="inicio"` + `LowBalanceBanner` (mesmo padrão das demais páginas do painel) + `DashboardPanel`.
- **`DashboardPanel`** (client component, via `backendFetch("dashboard")`): estados de loading ("Carregando...") e erro ("Não foi possível carregar o painel.") no padrão dos painéis existentes; grid de stat tiles + lista de conversas recentes.
  - Tile de saldo com tom crítico (`text-danger`) quando `credit_balance <= 0`; neutro caso contrário. Link pra `/creditos`.
  - Tile de WhatsApp: "Conectado" (verde/accent, número mascarado) ou "Desconectado" (crítico), link pra `/configuracoes/whatsapp`.
  - Tile de conversas aguardando humano com tom de atenção (latão/brass) quando `waiting_human > 0`.
  - Tile de KB com contagem de erros em tom crítico quando `error > 0`, link pra `/base-de-conhecimento`.
  - Conversas recentes: contato, estado (`agente`/`humano`, mesmo vocabulário do painel de conversas), última atividade; cada linha linka pra `/conversas`.
- **`TenantNav`**: ganha o item **"Início"** (`/inicio`) como primeiro da lista — ordem: Início / Conversas / Base / Config / Créditos. Tipo `TenantNavItem` ganha `"inicio"`.
- **Pós-login**: `apps/web/src/app/login/actions.ts` (`redirect("/inicio")`), `apps/web/src/middleware.ts` (2 redirects → `/inicio`, matcher ganha `"/inicio/:path*"`).
- **Proxy**: allowlist do `/api/backend/*` ganha o prefixo `"dashboard"`.

## Erros e casos-limite

| Caso | Comportamento |
|---|---|
| Tenant recém-criado (sem conversas/KB/consumo) | Todas as contagens em 0, lista de recentes vazia com mensagem neutra ("Nenhuma conversa ainda") |
| Sem WhatsApp conectado | Tile "Desconectado" com link pro setup — não é erro |
| Falha no fetch do dashboard | Mensagem de erro no painel (padrão dos demais), nav/banner continuam funcionais |
| Sem sessão | Middleware redireciona pra `/login` (matcher novo) |

## Testes

- **api**: `build_tenant_dashboard` com mocks posicionais (mesmo padrão do teste do admin dashboard — ordem de chamadas travada); rota 401 sem token e 200 com payload esperado; toda query filtrada por `tenant_id` (asserção de isolamento, padrão do fix do `billing/status`).
- **web**: `DashboardPanel` renderiza as métricas e a lista com dados mockados; estado de erro; links certos (`/creditos`, `/configuracoes/whatsapp`, `/base-de-conhecimento`, `/conversas`); `TenantNav` com o item "Início" ativo/inativo; redirects atualizados verificados por build (middleware não tem teste unitário — padrão existente).

## Fora de escopo desta entrega

- Gráficos/séries temporais (evolução futura).
- Polling/atualização automática.
- Personalização de widgets.
- Criação de `layout.tsx` compartilhado (débito pré-existente, o padrão por página continua).
