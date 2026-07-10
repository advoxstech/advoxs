# Design — RLS Efetivo em Produção (papel de banco não-owner)

Data: 2026-07-10
Status: aprovado

## Objetivo

Hoje o `api` e o `worker` conectam ao Postgres como `advoxs`, que é **owner** das tabelas — Row-Level Security não tem efeito nenhum sobre o dono de uma tabela, então as policies `tenant_isolation` criadas na migration `0001` (uma por tabela tenant-scoped, comparando `tenant_id` com `current_setting('app.tenant_id')`) são inertes desde que existem. A defesa em profundidade documentada no `CLAUDE.md` (RLS como camada extra além do filtro explícito em cada query) nunca esteve realmente ativa. Este spec faz a RLS entrar em vigor de verdade, sem quebrar os fluxos que **legitimamente** precisam ver mais de um tenant (login por e-mail, webhooks que resolvem o tenant no meio do caminho, idempotência de pagamento, painel de admin).

## Decisões de produto

- **Três papéis de banco, cada um com um propósito único** — least privilege: o papel que enxerga todos os tenants nunca tem permissão de DDL.
  - `advoxs` (existente) — owner das tabelas, usado **só** pelo Alembic (`alembic upgrade`). Deixa de ser usado em runtime pela aplicação.
  - `advoxs_app` (novo) — sem ownership, **sem** `BYPASSRLS`. Usado por `get_tenant_session` (rotas normais do tenant no `api`) e pelos jobs do `worker`.
  - `advoxs_system` (novo) — sem ownership, **com** `BYPASSRLS`. Usado pelas rotas que são cross-tenant por natureza: login (busca `User` por e-mail, que é único globalmente, antes de saber o tenant), signup/billing status (idempotência por `stripe_payment_id`, antes de o tenant existir ou vindo de qualquer tenant), os dois webhooks (`whatsapp`, `stripe` — resolvem/criam o tenant no meio da função), e todas as rotas de `/admin` (agregam todos os tenants por design).
- **Escopo inclui o `worker`**, não só o `api`. O `worker` já filtra manualmente por `tenant_id` em toda query (defesa de código), mas nunca seta `app.tenant_id` — sem isso, trocar o papel de conexão dele não teria efeito real de RLS. Cada job do worker já recebe `tenant_id` como parâmetro de entrada, então setar o contexto é direto (sem a complicação de "resolver o tenant no meio do caminho" que existe nos webhooks do `api`).
- **`get_session` (nome genérico, hoje usado por ~11 rotas cross-tenant) é renomeada para `get_system_session`** — sinaliza explicitamente no código que essa sessão vê todos os tenants por design. Mudança mecânica (só import/referência), sem lógica nova. Reduz o risco de alguém usar essa dependency por engano numa rota tenant-scoped nova no futuro — o projeto já teve um vazamento cross-tenant real desse tipo (`GET /billing/status`, corrigido em feature anterior).
- **Nenhuma mudança de código nos services que já resolvem o tenant no meio da função** (`services/whatsapp_inbound.py`, `services/billing.py`) — como essas rotas passam a usar `advoxs_system` (que ignora RLS por ser `BYPASSRLS`), continuam funcionando exatamente como hoje, sem precisar chamar `set_config` em nenhum ponto novo.
- **Criação dos papéis via migration Alembic**, não via `infra/postgres/init/002-databases.sh`. O script bash só roda na primeira criação do volume Postgres (mecanismo `docker-entrypoint-initdb.d`); como o Postgres de dev (e qualquer ambiente já em produção) já existe, só uma migration nova aplica em bancos já inicializados sem precisar recriar o volume.
- **Falha segura por padrão**: se algum código esquecer de setar `app.tenant_id` numa sessão `advoxs_app`, o resultado é ver **zero linhas** (nunca linhas de outro tenant) — a policy `USING`/`WITH CHECK` compara contra `current_setting(..., true)`, que retorna `NULL` quando a variável não foi setada, e `tenant_id = NULL` é sempre falso. É um modo de falha visível (quebra), não um vazamento silencioso.

## Migration nova (Postgres)

Uma migration Alembic (rodando com o papel `advoxs`, que continua owner):
- Cria `advoxs_app` (`LOGIN`, senha via env) e `advoxs_system` (`LOGIN`, senha via env, `BYPASSRLS`).
- `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO advoxs_app, advoxs_system` — ambos precisam do grant de tabela independente de `BYPASSRLS` (que só afeta a visibilidade de linha, não o privilégio de tabela).
- `ALTER DEFAULT PRIVILEGES FOR ROLE advoxs IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO advoxs_app, advoxs_system` — garante que tabelas criadas por migrations *futuras* (owned by `advoxs`) já nascem com o grant certo, sem precisar lembrar de repetir isso manualmente em cada migration nova (pegadinha clássica de RLS: esquecer o grant numa tabela nova e ela ficar inacessível em silêncio pros dois papéis novos).
- `downgrade()` reverte: revoga os grants e os default privileges, depois derruba os dois papéis.
- Sem `FORCE ROW LEVEL SECURITY` nas tabelas — não é necessário, já que nenhum papel de runtime é mais owner.

Senhas via env nova (`APP_DB_PASSWORD`, `SYSTEM_DB_PASSWORD`), mesmo padrão já usado por `AGENTS_DB_PASSWORD`/`RAG_DB_PASSWORD`.

## `apps/api` — engines e dependencies

- `DATABASE_URL` continua existindo com o significado atual (**owner**) — usada só pelo Alembic (`alembic/env.py` sem nenhuma mudança).
- Duas envs novas: `APP_DATABASE_URL` (aponta pra `advoxs_app`) e `SYSTEM_DATABASE_URL` (aponta pra `advoxs_system`).
- `app/core/db.py` passa a expor dois engines/session factories: `SessionLocal` (papel `advoxs_app`, RLS ativo — usado por `get_tenant_session`) e `SystemSessionLocal` (papel `advoxs_system`, bypass — usado pela nova `get_system_session`).
- `get_session` é renomeada para `get_system_session` em todos os call sites atuais: `auth.py` (login/refresh), `billing.py` (`/balance`, `/checkout` — `/status` já usa `get_tenant_session`, sem mudança), `credit_packages.py`, `signup.py`, `webhooks/stripe.py`, `webhooks/whatsapp.py`, e as 4 rotas de `platform_admin/*.py`.
- `scripts/seed_dev.py` (ferramenta de dev, nunca roda em produção) passa a importar `SystemSessionLocal` em vez de `SessionLocal` — evita ensinar o script a fazer `set_config` pra um fluxo que só existe em ambiente local. `scripts/seed_platform_admin.py` não precisa de mudança (só toca `platform_admins`, tabela global sem RLS).

## `apps/worker` — sessão com contexto de tenant

- `DATABASE_URL` do worker passa a apontar pra `advoxs_app` (env renomeada pra `APP_DATABASE_URL`, mesmo valor usado pelo `api` pra esse papel).
- Helper novo `open_tenant_session(session_factory, tenant_id)` — abre a sessão e já faz `set_config('app.tenant_id', tenant_id, true)` (mesmo padrão do `get_tenant_session` do `api`, `is_local=true` pra valer só na transação atual). Substitui o `async with session_factory() as session:` cru em todo ponto de `process_inbound_message` e `ingest_knowledge_base_file` que toca tabela tenant-scoped.
- Pensado pra ser fácil de mockar nos testes existentes — mesma interface de context manager que `session_factory()` já tem hoje (`__aenter__`/`__aexit__`), só que já injeta o `set_config` internamente.

## Testes

- **Testes existentes** (mockam a sessão inteira): mudança mecânica — atualizar o import de `get_session` pra `get_system_session` nos arquivos de teste correspondentes do `api`; adaptar os mocks de `session_factory` do `worker` pro novo helper `open_tenant_session`.
- **Teste de integração novo** (`apps/api/tests/integration/`, primeiro teste real dessa pasta — hoje só tem `.gitkeep`): conecta num Postgres real (mesmo do `docker compose` de dev, via env), cria dados de 2 tenants numa tabela tenant-scoped, e prova três coisas que nenhum teste mockado consegue provar:
  1. Uma sessão como `advoxs_app` com `app.tenant_id` setado pro tenant A só vê as linhas do tenant A — **mesmo sem nenhum filtro `WHERE` explícito na query**.
  2. Uma sessão como `advoxs_system` vê os dois tenants (bypass funcionando).
  3. Inserir uma linha com `tenant_id` diferente do `app.tenant_id` setado é **rejeitado pelo Postgres** (`WITH CHECK`).
- Esse teste de integração só roda localmente (precisa de Postgres real de pé) — não entra no CI agora, já que a infra de Postgres de teste no CI é uma pendência separada e aberta no `CLAUDE.md`, fora do escopo deste spec.

## Rollout

A migration (que cria os papéis) precisa rodar **antes** de trocar as envs `APP_DATABASE_URL`/`SYSTEM_DATABASE_URL` e reiniciar os containers — senão a aplicação não consegue nem conectar. Em produção isso já é garantido pelo `deploy.yml` (migrations rodam antes de subir o `api`). Localmente: aplicar a migration primeiro, só depois atualizar o `.env` e reiniciar `api`/`worker`.

## Erros e casos-limite

| Caso | Comportamento |
|---|---|
| Código esquece de setar `app.tenant_id` numa sessão `advoxs_app` | Zero linhas visíveis (falha segura, nunca vaza dado de outro tenant) |
| `INSERT` com `tenant_id` diferente do `app.tenant_id` setado | Rejeitado pelo Postgres (`new row violates row-level security policy`) |
| Migration futura cria uma tabela nova sem grant explícito | Já nasce acessível pros dois papéis novos, via `ALTER DEFAULT PRIVILEGES` |
| `downgrade()` da migration | Revoga grants/default privileges antes de derrubar os papéis (evita erro de "role tem privilégios pendentes") |

## Fora de escopo desta entrega

- `apps/agents`/`apps/api_rag` — bancos próprios (`advoxs_agents`, `advoxs_rag`), sem relação com a RLS do banco `advoxs`.
- Rodar o teste de integração novo no CI (a infra de Postgres de teste no CI já é uma pendência aberta e separada no `CLAUDE.md`).
- Qualquer tooling automático pra impedir uso indevido futuro de `get_system_session` numa rota tenant-scoped nova — fica só a convenção de nome como sinalização; revisão de código continua sendo a defesa real.
- `FORCE ROW LEVEL SECURITY` — não necessário, já que nenhum papel de runtime é mais owner das tabelas.
