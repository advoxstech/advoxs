# Design — Painel de Administração da Plataforma (`/admin`)

Data: 2026-07-09
Status: aprovado

## Objetivo

Área de back-office da empresa fornecedora (Advoxs), separada do painel dos escritórios, para gerenciar/visualizar todos os tenants e acompanhar métricas agregadas da plataforma (vendas, consumo, crescimento). Escopo desta entrega: **somente leitura** — dashboard agregado + lista/detalhe de tenants, sem ações (suspender, creditar manualmente).

## Decisões de produto

- **Sem ações nesta entrega** — só leitura, conforme já definido no CLAUDE.md ("Escopo atual: somente leitura"). Suspender/creditar ficam para evolução futura.
- **Provisionamento do `platform_admin` via script** (`scripts/seed_platform_admin.py`, mesmo padrão do `seed_dev.py`) — não é um cadastro público, é back-office interno.
- **Sessão do admin totalmente isolada da do tenant**: cookies com nomes diferentes, JWT com `type` e secret diferentes, proxy próprio no Next. Uma sessão de tenant nunca concede acesso a `/admin` e vice-versa.
- **Métricas calculadas na hora** (queries agregadas simples), sem view materializada — o volume de dados atual não justifica esse investimento.
- **Auditoria só na leitura de um tenant específico** (`GET /platform-admin/tenants/{id}`) — a listagem agregada e o dashboard não drilham num tenant específico, então não precisam de auditoria (interpretação do requisito "toda leitura de dado de um tenant específico pelo super-admin deve ser auditada" do CLAUDE.md).

## Decisão de arquitetura: RLS inerte por conexão como owner (simplificação temporária)

O `api` conecta ao Postgres como `advoxs`, que é o **owner** das tabelas (criadas pelas próprias migrations desse role). RLS não tem efeito nenhum sobre o owner de uma tabela, independente de `app.tenant_id` estar setado ou não — isso já é uma pendência registrada no CLAUDE.md ("RLS só tem efeito para papéis de banco que não sejam donos das tabelas"). Por isso, as rotas do admin desta entrega usam a mesma dependency `get_session` simples (sem tenant_id), sem precisar de um role `BYPASSRLS` dedicado.

**Isso é uma simplificação presa ao estado atual da infraestrutura, não uma decisão de arquitetura permanente.** Quando a pendência de conectar o `api` com um role não-owner for resolvida (item já registrado no CLAUDE.md), as rotas do admin precisarão ser revisadas para usar uma sessão com `BYPASSRLS` explícito ou uma role de leitura dedicada — documentar essa dependência cruzada no CLAUDE.md ao implementar.

## Autenticação do `platform_admin`

- JWT assinado com secret **separado** do `JWT_SECRET` dos tenants — nova env `PLATFORM_JWT_SECRET`. Defesa em profundidade: um `JWT_SECRET` de tenant vazado nunca forja um token de admin, e vice-versa.
- `type: "platform_access"` / `"platform_refresh"` no payload (em vez de `"access"`/`"refresh"`) — impede reuso cruzado mesmo que os dois secrets algum dia colidissem por erro de config.
- Mesmo mecanismo de rotação de refresh token + blacklist no Redis já usado no auth de tenant (`auth:blacklist:{jti}`), mas com um prefixo de chave separado (`platform_auth:blacklist:{jti}`) para nunca colidir com blacklist de tenant.
- Rotas: `POST /api/v1/platform-admin/auth/{login,refresh,logout}` — mesma forma de `POST /api/v1/auth/{login,refresh,logout}`, isolada por prefixo.
- Dependency nova `get_current_platform_admin` (mesmo padrão de `get_current_tenant`, decodificando com `PLATFORM_JWT_SECRET`).

## Dashboard agregado

`GET /api/v1/platform-admin/dashboard`, autenticado via `get_current_platform_admin`. Métricas e suas fontes:

| Métrica | Fonte |
|---|---|
| Tenants totais / ativos / suspensos | `COUNT(*) GROUP BY status` em `tenants` |
| Novos escritórios (últimos 30 dias, por dia) | `COUNT(*) GROUP BY date_trunc('day', created_at)` em `tenants`, filtrado aos últimos 30 dias |
| Créditos vendidos vs consumidos | `SUM(amount_credits) GROUP BY type` em `credit_transactions` (`purchase` vs `consumption`) |
| Receita (R$) por período | `SUM` de `credit_transactions.amount_credits` convertido via preço/créditos do `credit_packages` associado (ver ressalva abaixo) |
| Mensagens processadas | `COUNT(*)` em `messages` |
| Execuções de agente | `COUNT(*)` em `messages WHERE tokens_used IS NOT NULL` — o worker grava `tokens_used` só na primeira mensagem de cada execução, então essa contagem aproxima o número de execuções |
| Tokens consumidos | `SUM(tokens_used)` em `messages` |
| Escritórios com menor saldo | `tenants` ordenado por `credit_balance ASC`, top 10 — sem threshold fixo de "saldo baixo" (evita bikeshedding de calibração) |
| WhatsApp conectados | `COUNT(*)` em `whatsapp_numbers WHERE status='connected'`, sobre o total de tenants |
| Uso de base de conhecimento | `COUNT(*)`/`SUM(size_bytes)` agregados de `knowledge_base_files` |

**Ressalva sobre receita**: `credit_transactions` não guarda o preço pago no momento da compra, só o `credit_package_id` — se o preço de um pacote mudar depois, o cálculo via join reflete o preço **atual** do pacote, não o histórico real da venda. Aceitável para esta entrega (pacotes não devem mudar de preço com frequência); precisão contábil histórica exigiria uma coluna `price_brl_paid` em `credit_transactions` (mudança de schema fora de escopo aqui).

## Lista e detalhe de tenants

- **`GET /api/v1/platform-admin/tenants`** — lista paginada: nome, status, `credit_balance`, data de criação, se tem WhatsApp conectado.
- **`GET /api/v1/platform-admin/tenants/{id}`** — detalhe: dados do tenant + últimas transações de crédito (`credit_transactions`) + arquivos de base de conhecimento (`knowledge_base_files`). **Toda chamada grava uma linha em `admin_audit_logs`** (tabela nova via migration: `id`, `platform_admin_id` FK, `tenant_id` FK, `created_at`) — implementa a exigência de auditoria do CLAUDE.md para esse caso específico.

## Frontend (`web`)

- **Proxy dedicado**: `/api/admin-backend/*` (novo route handler, análogo ao `/api/backend/*` dos tenants, mas com cookies próprios `platform_access_token`/`platform_refresh_token` e refresh contra `/api/v1/platform-admin/auth/refresh`). Nunca reaproveita o proxy dos tenants.
- **`/admin/login`** — formulário simples (e-mail + senha), mesmo padrão visual de `/login`.
- **`/admin`** — dashboard com stat tiles para cada métrica da seção anterior.
- **`/admin/tenants`** — tabela da listagem.
- **`/admin/tenants/[id]`** — página de detalhe.
- **Middleware**: bloco novo e independente para `pathname.startsWith("/admin")`, checando exclusivamente os cookies do platform admin — nunca interage com a lógica de sessão de tenant já existente no mesmo arquivo, e vice-versa (dois blocos condicionais paralelos, não um só compartilhado).

## Erros e casos-limite

| Caso | Comportamento |
|---|---|
| Token de tenant usado em rota `/platform-admin/*` | `401` (secret/`type` diferentes — decodificação falha) |
| Token de platform_admin usado em rota de tenant | `401` (mesmo motivo, ao contrário) |
| `GET /tenants/{id}` com id inexistente | `404` |
| Acesso a `/admin/*` sem sessão de platform_admin | Middleware redireciona pra `/admin/login` |
| Acesso a `/admin/login` já com sessão de platform_admin | Middleware redireciona pra `/admin` |

## Testes

- **api**: auth do platform_admin (login/refresh/logout, secret separado, blacklist com prefixo próprio); dashboard (cada métrica com dado mockado); listagem de tenants (paginação); detalhe de tenant (404 se não existe); **auditoria gravada no detalhe** — teste explícito checando o INSERT em `admin_audit_logs` ao chamar `GET /tenants/{id}`.
- **web**: allowlist do proxy `/api/admin-backend/*`; middleware (bloco `/admin` isolado do bloco de tenant, nos dois sentidos); páginas de login/dashboard/lista/detalhe.

## Fora de escopo desta entrega

- Ações (suspender tenant, creditar manualmente) — modelo de dados já comporta, implementação fica para depois.
- Subdomínio dedicado (`admin.advoxs.com.br`) — fica em `/admin` dentro do mesmo `apps/web` por ora, já pensado para migrar sem refatorar o modelo.
- Precisão contábil histórica de receita (coluna `price_brl_paid` em `credit_transactions`).
- Papéis diferenciados dentro de `platform_admins` (hoje só existe `superadmin`).
