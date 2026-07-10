# RLS Efetivo em Produção Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer as policies de RLS (já existentes desde a migration `0001`) entrarem em vigor de verdade, trocando o papel de conexão de runtime do `api`/`worker` de `advoxs` (owner, RLS inerte) para um papel novo sem ownership — sem quebrar login, signup, webhooks, painel de admin ou os jobs do worker, que legitimamente precisam ver mais de um tenant ou ainda não sabem qual tenant é no momento em que a sessão abre.

**Architecture:** Três papéis de banco: `advoxs` (owner, só migrations), `advoxs_app` (sem ownership, RLS ativo — rotas tenant-scoped do `api` via `get_tenant_session`/`get_session`, e jobs do `worker` via um helper novo `open_tenant_session`) e `advoxs_system` (sem ownership, `BYPASSRLS` — rotas genuinamente cross-tenant do `api`, renomeando `get_session` para `get_system_session` nesses 10 call sites). A migration cria os papéis e os grants (incluindo `ALTER DEFAULT PRIVILEGES` pra tabelas futuras). Um teste de integração novo prova contra um Postgres real que a RLS bloqueia leitura/escrita cross-tenant pro papel `advoxs_app` e que `advoxs_system` continua vendo tudo.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (api), Arq + SQLAlchemy Core (worker), Postgres 16.

## Global Constraints

- Três papéis: `advoxs` (owner, só Alembic), `advoxs_app` (sem ownership, sem `BYPASSRLS`), `advoxs_system` (sem ownership, com `BYPASSRLS`).
- `get_session` (nome existente) passa a apontar pro papel `advoxs_app` e continua sendo usada internamente por `get_tenant_session` — **sem mudança de nome nem de código em `deps.py`**, só troca o engine por trás.
- `get_system_session` (nome novo) aponta pro papel `advoxs_system` — substitui `get_session` nos 10 call sites que são cross-tenant por natureza: `auth.py`, `billing.py` (`/balance`, `/checkout` — `/status` já usa `get_tenant_session`, sem mudança), `credit_packages.py`, `signup.py`, `webhooks/stripe.py`, `webhooks/whatsapp.py`, e as 4 rotas de `platform_admin/*.py`.
- Nenhuma mudança de código nos services que resolvem o tenant no meio da função (`services/whatsapp_inbound.py`, `services/billing.py`) — o papel `advoxs_system` já ignora RLS, então continuam funcionando como hoje.
- `worker` também entra no escopo: cada job já recebe `tenant_id` como parâmetro, então um helper novo (`open_tenant_session`) seta `app.tenant_id` antes de qualquer query tenant-scoped.
- Falha segura por padrão: se algum código esquecer de setar `app.tenant_id` numa sessão `advoxs_app`, o resultado é zero linhas visíveis, nunca dado de outro tenant.
- `DATABASE_URL` mantém o significado atual (owner) — usada só pelo Alembic, sem mudança em `alembic/env.py`.
- Envs novas: `APP_DB_PASSWORD`, `SYSTEM_DB_PASSWORD` (senhas dos papéis, lidas pela migration), `APP_DATABASE_URL`, `SYSTEM_DATABASE_URL` (connection strings completas, mesmo padrão de `DATABASE_URL` — senha literal na URL, sem interpolação de variável, já que `env_file` do Docker Compose não expande `${VAR}`).
- Teste de integração novo (`apps/api/tests/integration/`) só roda localmente contra um Postgres real — pula com `pytest.skip` se não conseguir conectar; não entra no CI.
- Mensagens/comentários em pt-BR com acentuação correta.
- Comandos: `apps/api` → `uv run pytest tests/unit`, `uv run pytest tests/integration` (precisa de Postgres real), `uv run ruff check .`, `uv run ruff format --check .`. `apps/worker` → mesmos comandos, dentro de `apps/worker`.

---

### Task 1: Migration `0008` — papéis, grants e `CONNECT`

**Files:**
- Create: `apps/api/alembic/versions/0008_tenant_isolation_roles.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: nada de outras tasks.
- Produces: papéis `advoxs_app` (sem ownership, sem `BYPASSRLS`) e `advoxs_system` (sem ownership, com `BYPASSRLS`) no Postgres, com `GRANT SELECT, INSERT, UPDATE, DELETE` em todas as tabelas + `CONNECT` no database + `ALTER DEFAULT PRIVILEGES` pra tabelas futuras. Nenhuma interface de código Python — só efeito no banco.

- [ ] **Step 1: Escrever a migration**

Criar `apps/api/alembic/versions/0008_tenant_isolation_roles.py`:

```python
"""papéis de banco não-owner para RLS efetiva (advoxs_app, advoxs_system)

`advoxs` (owner) passa a ser usado só pelo Alembic (DDL) — as policies de
RLS da migration 0001 não têm efeito sobre o owner de uma tabela. Dois
papéis novos, sem ownership:

- advoxs_app (sem BYPASSRLS): rotas tenant-scoped do api e jobs do
  worker — aqui a RLS entra em vigor de verdade.
- advoxs_system (com BYPASSRLS): rotas que legitimamente veem mais de
  um tenant (login por e-mail, webhooks, idempotência de pagamento,
  painel de admin).

`ALTER DEFAULT PRIVILEGES` garante que tabelas criadas por migrations
futuras (owned by advoxs) já nascem acessíveis pros dois papéis novos,
sem precisar lembrar de repetir o GRANT manualmente a cada migration.

`GRANT CONNECT` é necessário porque infra/postgres/init/002-databases.sh
já revogou CONNECT de PUBLIC no database advoxs — sem isso, os papéis
novos não conseguem nem abrir uma conexão.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-10
"""

import os

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

APP_ROLE = "advoxs_app"
SYSTEM_ROLE = "advoxs_system"


def upgrade() -> None:
    database_name = op.get_bind().engine.url.database
    app_password = os.getenv("APP_DB_PASSWORD", "changeme")
    system_password = os.getenv("SYSTEM_DB_PASSWORD", "changeme")

    op.execute(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{app_password}'")
    op.execute(f"CREATE ROLE {SYSTEM_ROLE} LOGIN PASSWORD '{system_password}' BYPASSRLS")

    op.execute(f'GRANT CONNECT ON DATABASE "{database_name}" TO {APP_ROLE}, {SYSTEM_ROLE}')
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        f"TO {APP_ROLE}, {SYSTEM_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE advoxs IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}, {SYSTEM_ROLE}"
    )


def downgrade() -> None:
    database_name = op.get_bind().engine.url.database

    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE advoxs IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE}, {SYSTEM_ROLE}"
    )
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}, {SYSTEM_ROLE}")
    op.execute(f'REVOKE CONNECT ON DATABASE "{database_name}" FROM {APP_ROLE}, {SYSTEM_ROLE}')
    op.execute(f"DROP ROLE {APP_ROLE}")
    op.execute(f"DROP ROLE {SYSTEM_ROLE}")
```

- [ ] **Step 2: Validar a sintaxe**

Run: `cd apps/api && python3 -c "import ast; ast.parse(open('alembic/versions/0008_tenant_isolation_roles.py').read())" && echo "sintaxe ok"`
Expected: `sintaxe ok`.

- [ ] **Step 3: Adicionar as senhas ao `.env.example`**

Em `.env.example`, na seção do Postgres (após a linha `RAG_DB_PASSWORD=changeme`), adicionar:

```dotenv
# Papéis não-owner pra RLS efetiva (ver migration 0008) — advoxs_app (sem
# BYPASSRLS, rotas tenant-scoped) e advoxs_system (com BYPASSRLS, rotas
# cross-tenant: login, webhooks, admin).
APP_DB_PASSWORD=changeme
SYSTEM_DB_PASSWORD=changeme
```

- [ ] **Step 4: Aplicar a migration no Postgres real de dev e verificar**

Run: `docker compose exec api uv run alembic upgrade head`
Expected: log mostrando `Running upgrade 0007 -> 0008, papéis de banco não-owner para RLS efetiva`, sem erro.

Run: `docker compose exec -T postgres psql -U advoxs -d advoxs -c "\du advoxs_app advoxs_system"`
Expected: lista os dois papéis; `advoxs_system` mostra `Bypass RLS` na coluna de atributos, `advoxs_app` não mostra esse atributo.

Run: `docker compose exec -T postgres psql -U advoxs -d advoxs -c "SELECT grantee, privilege_type FROM information_schema.role_table_grants WHERE table_name = 'conversations' AND grantee IN ('advoxs_app', 'advoxs_system') ORDER BY grantee, privilege_type;"`
Expected: 8 linhas (4 privilégios × 2 papéis: `DELETE`, `INSERT`, `SELECT`, `UPDATE`).

- [ ] **Step 5: Confirmar que o downgrade e o upgrade de novo funcionam limpos**

Run: `docker compose exec api uv run alembic downgrade -1`
Expected: `Running downgrade 0008 -> 0007`, sem erro.

Run: `docker compose exec -T postgres psql -U advoxs -d advoxs -c "\du advoxs_app advoxs_system"`
Expected: `Role "advoxs_app" does not exist.` / `Role "advoxs_system" does not exist.` (confirma que o downgrade limpou tudo).

Run: `docker compose exec api uv run alembic upgrade head`
Expected: recria os papéis sem erro (confirma que não há resíduo do downgrade travando a recriação).

- [ ] **Step 6: Commit**

```bash
git add apps/api/alembic/versions/0008_tenant_isolation_roles.py .env.example
git commit -m "feat(api): migration cria papéis de banco não-owner (advoxs_app, advoxs_system)"
```

---

### Task 2: `apps/api` — engines separados e `get_system_session`

**Files:**
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/core/db.py`
- Modify: `apps/api/scripts/seed_dev.py`
- Modify: `.env.example`
- Test: `apps/api/tests/unit/test_db.py` (create)

**Interfaces:**
- Consumes: papéis `advoxs_app`/`advoxs_system` (Task 1, já existem no Postgres de dev).
- Produces: `settings.app_database_url`/`settings.system_database_url` (novos campos); `app.core.db.engine`/`SessionLocal` (agora backed by `advoxs_app`, mesmos nomes de hoje — `get_tenant_session` em `deps.py` **não precisa mudar**); `app.core.db.system_engine`/`SystemSessionLocal`/`get_system_session` (novos, backed by `advoxs_system`) — usados pela Task 3.

- [ ] **Step 1: Adicionar as envs de connection string ao `.env.example`**

Em `.env.example`, logo após a linha `DATABASE_URL=postgresql+asyncpg://advoxs:changeme@postgres:5432/advoxs`, adicionar:

```dotenv
# advoxs_app (RLS ativo — rotas tenant-scoped do api e jobs do worker) e
# advoxs_system (BYPASSRLS — login, webhooks, idempotência de pagamento,
# painel de admin). Papéis criados pela migration 0008; a senha aqui
# precisa bater com APP_DB_PASSWORD/SYSTEM_DB_PASSWORD.
APP_DATABASE_URL=postgresql+asyncpg://advoxs_app:changeme@postgres:5432/advoxs
SYSTEM_DATABASE_URL=postgresql+asyncpg://advoxs_system:changeme@postgres:5432/advoxs
```

- [ ] **Step 2: Adicionar os campos novos ao `Settings`**

Em `apps/api/app/core/config.py`, trocar:

```python
    database_url: str
    redis_url: str
```

por:

```python
    # Owner das tabelas — usada só pelo Alembic (DDL); a app não conecta
    # mais com esse papel em runtime.
    database_url: str
    # advoxs_app (RLS ativo) e advoxs_system (BYPASSRLS) — ver migration
    # 0008 e app/core/db.py.
    app_database_url: str
    system_database_url: str
    redis_url: str
```

- [ ] **Step 3: Escrever o teste que falha**

Criar `apps/api/tests/unit/test_db.py`:

```python
from app.core.db import engine, get_session, get_system_session, system_engine


def test_engine_e_system_engine_sao_conexoes_distintas() -> None:
    assert engine is not system_engine
    assert str(engine.url) != str(system_engine.url)


def test_engine_usa_app_database_url() -> None:
    assert engine.url.username == "advoxs_app"
    assert engine.url.database == "advoxs"


def test_system_engine_usa_system_database_url() -> None:
    assert system_engine.url.username == "advoxs_system"


def test_get_session_e_get_system_session_sao_funcoes_distintas() -> None:
    assert get_session is not get_system_session
```

- [ ] **Step 4: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_db.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_system_session' from 'app.core.db'` (ainda não existe).

- [ ] **Step 5: Implementar os dois engines em `db.py`**

Substituir o conteúdo de `apps/api/app/core/db.py` por:

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# advoxs_app — RLS ativo, usado pelas rotas tenant-scoped (via
# get_tenant_session, em app/api/deps.py) e nunca diretamente por rota
# nenhuma sem antes setar app.tenant_id.
engine = create_async_engine(settings.app_database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# advoxs_system — BYPASSRLS, usado pelas rotas genuinamente cross-tenant
# (login, webhooks, idempotência de pagamento, painel de admin).
system_engine = create_async_engine(settings.system_database_url, pool_pre_ping=True)
SystemSessionLocal = async_sessionmaker(system_engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_system_session() -> AsyncIterator[AsyncSession]:
    async with SystemSessionLocal() as session:
        yield session
```

- [ ] **Step 6: Rodar e ver passar**

Run: `cd apps/api && uv run pytest tests/unit/test_db.py -v`
Expected: PASS (4/4).

- [ ] **Step 7: Repontar `seed_dev.py` pro papel system**

Em `apps/api/scripts/seed_dev.py`, trocar a linha:

```python
from app.core.db import SessionLocal
```

por:

```python
from app.core.db import SystemSessionLocal
```

E trocar `async with SessionLocal() as session:` por `async with SystemSessionLocal() as session:` (única ocorrência, dentro de `async def seed`). Justificativa (não precisa de comentário no código, só aqui no plano): o script cria `User`/`WhatsAppNumber` (tenant-scoped) sem nunca setar `app.tenant_id` — é ferramenta de dev, nunca roda em produção, então usar o papel bypass é mais simples do que ensinar o script a fazer `set_config`.

- [ ] **Step 8: Rodar a suíte completa e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos PASS, ruff limpo. (A suíte completa ainda depende de `APP_DATABASE_URL`/`SYSTEM_DATABASE_URL` estarem setadas no `.env` local — se os testes unitários falharem por `pydantic.ValidationError` reclamando de campo faltando, adicione as duas envs ao `.env` local com os mesmos valores do `.env.example`, apontando pro Postgres de dev.)

- [ ] **Step 9: Commit**

```bash
git add apps/api/app/core/config.py apps/api/app/core/db.py apps/api/scripts/seed_dev.py apps/api/tests/unit/test_db.py .env.example
git commit -m "feat(api): engines separados por papel (advoxs_app/advoxs_system) em app/core/db.py"
```

---

### Task 3: `apps/api` — rotas cross-tenant migram pra `get_system_session`

**Files:**
- Modify: `apps/api/app/api/v1/auth.py`
- Modify: `apps/api/app/api/v1/billing.py`
- Modify: `apps/api/app/api/v1/credit_packages.py`
- Modify: `apps/api/app/api/v1/signup.py`
- Modify: `apps/api/app/api/v1/webhooks/stripe.py`
- Modify: `apps/api/app/api/v1/webhooks/whatsapp.py`
- Modify: `apps/api/app/api/v1/platform_admin/auth.py`
- Modify: `apps/api/app/api/v1/platform_admin/dashboard.py`
- Modify: `apps/api/app/api/v1/platform_admin/tenants.py`
- Modify: `apps/api/app/api/v1/platform_admin/playground.py`
- Modify: `apps/api/tests/unit/test_auth_routes.py`
- Modify: `apps/api/tests/unit/test_billing_routes.py`
- Modify: `apps/api/tests/unit/test_credit_packages_routes.py`
- Modify: `apps/api/tests/unit/test_signup_routes.py`
- Modify: `apps/api/tests/unit/test_stripe_webhook.py`
- Modify: `apps/api/tests/unit/test_whatsapp_webhook.py`
- Modify: `apps/api/tests/unit/test_platform_admin_auth_routes.py`
- Modify: `apps/api/tests/unit/test_admin_dashboard_routes.py`
- Modify: `apps/api/tests/unit/test_admin_tenants_routes.py`
- Modify: `apps/api/tests/unit/test_playground_routes.py`

**Interfaces:**
- Consumes: `get_system_session` de `app.core.db` (Task 2).
- Produces: nenhuma interface nova — é o ponto final da migração dessas 10 rotas.

Este é um rename mecânico e idêntico nos 10 pares (rota + teste). Cada rota troca APENAS o `import`/`Depends`; cada teste troca APENAS o `import`/`dependency_overrides`. Sem mudança de lógica em nenhum arquivo.

- [ ] **Step 1: Escrever os testes que falham (atualizar os 10 arquivos de teste)**

Em cada um dos 10 arquivos de teste abaixo, trocar a linha de import e a linha de `dependency_overrides`:

`apps/api/tests/unit/test_auth_routes.py` — linha 7: `from app.core.db import get_session` → `from app.core.db import get_system_session`; linha 55: `app.dependency_overrides[get_session] = override_session` → `app.dependency_overrides[get_system_session] = override_session`.

`apps/api/tests/unit/test_billing_routes.py` — linha 9: mesma troca de import; linha 31: mesma troca de `dependency_overrides`.

`apps/api/tests/unit/test_credit_packages_routes.py` — linha 8: mesma troca de import; linha 31: mesma troca de `dependency_overrides`.

`apps/api/tests/unit/test_signup_routes.py` — linha 8: mesma troca de import; linha 32: mesma troca de `dependency_overrides`.

`apps/api/tests/unit/test_stripe_webhook.py` — linha 7: mesma troca de import; linha 21: mesma troca de `dependency_overrides`.

`apps/api/tests/unit/test_whatsapp_webhook.py` — linha 11: mesma troca de import; linha 63: mesma troca de `dependency_overrides`.

`apps/api/tests/unit/test_platform_admin_auth_routes.py` — linha 10: mesma troca de import; linha 50: mesma troca de `dependency_overrides`.

`apps/api/tests/unit/test_admin_dashboard_routes.py` — linha 8: mesma troca de import; linha 51: mesma troca de `dependency_overrides`.

`apps/api/tests/unit/test_admin_tenants_routes.py` — linha 8: mesma troca de import; linha 22: mesma troca de `dependency_overrides`.

`apps/api/tests/unit/test_playground_routes.py` — linha 9: mesma troca de import; linha 26: mesma troca de `dependency_overrides`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_auth_routes.py tests/unit/test_billing_routes.py tests/unit/test_credit_packages_routes.py tests/unit/test_signup_routes.py tests/unit/test_stripe_webhook.py tests/unit/test_whatsapp_webhook.py tests/unit/test_platform_admin_auth_routes.py tests/unit/test_admin_dashboard_routes.py tests/unit/test_admin_tenants_routes.py tests/unit/test_playground_routes.py -v`
Expected: FAIL em todos — `ImportError: cannot import name 'get_system_session' from 'app.core.db'` (a rota ainda usa `get_session`, o nome novo ainda não existe nas rotas).

- [ ] **Step 3: Atualizar as 10 rotas**

`apps/api/app/api/v1/auth.py` — linha 5: `from app.core.db import get_session` → `from app.core.db import get_system_session`; linhas 16 e 25: `Depends(get_session)` → `Depends(get_system_session)`.

`apps/api/app/api/v1/billing.py` — linha 8: mesma troca de import; linhas 28 e 38: `Depends(get_session)` → `Depends(get_system_session)` (a rota `/status`, linha 56, já usa `get_tenant_session` — não toca).

`apps/api/app/api/v1/credit_packages.py` — linha 7: mesma troca de import; linha 16: `Depends(get_session)` → `Depends(get_system_session)`.

`apps/api/app/api/v1/signup.py` — linha 7: mesma troca de import; linhas 23 e 42: `Depends(get_session)` → `Depends(get_system_session)`.

`apps/api/app/api/v1/webhooks/stripe.py` — linha 10: mesma troca de import; linha 22: `Depends(get_session)` → `Depends(get_system_session)`.

`apps/api/app/api/v1/webhooks/whatsapp.py` — linha 11: mesma troca de import; linha 38: `Depends(get_session)` → `Depends(get_system_session)`.

`apps/api/app/api/v1/platform_admin/auth.py` — linha 5: mesma troca de import; linhas 20 e 31: `Depends(get_session)` → `Depends(get_system_session)`.

`apps/api/app/api/v1/platform_admin/dashboard.py` — linha 5: mesma troca de import; linha 15: `Depends(get_session)` → `Depends(get_system_session)`.

`apps/api/app/api/v1/platform_admin/tenants.py` — linha 7: mesma troca de import; linhas 19 e 28: `Depends(get_session)` → `Depends(get_system_session)`.

`apps/api/app/api/v1/platform_admin/playground.py` — linha 8: mesma troca de import; linha 21: `Depends(get_session)` → `Depends(get_system_session)` (única ocorrência no arquivo — a segunda rota do arquivo não abre sessão de banco).

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/api && uv run pytest tests/unit -q`
Expected: todos os testes da suíte completa PASS (não só os 10 arquivos tocados — confirma que nenhuma outra rota dependia implicitamente de `get_session` continuar existindo com esse comportamento).

- [ ] **Step 5: Lint**

Run: `cd apps/api && uv run ruff check . && uv run ruff format --check .`
Expected: ambos limpos.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/api/v1/auth.py apps/api/app/api/v1/billing.py apps/api/app/api/v1/credit_packages.py apps/api/app/api/v1/signup.py apps/api/app/api/v1/webhooks/stripe.py apps/api/app/api/v1/webhooks/whatsapp.py apps/api/app/api/v1/platform_admin/auth.py apps/api/app/api/v1/platform_admin/dashboard.py apps/api/app/api/v1/platform_admin/tenants.py apps/api/app/api/v1/platform_admin/playground.py apps/api/tests/unit/test_auth_routes.py apps/api/tests/unit/test_billing_routes.py apps/api/tests/unit/test_credit_packages_routes.py apps/api/tests/unit/test_signup_routes.py apps/api/tests/unit/test_stripe_webhook.py apps/api/tests/unit/test_whatsapp_webhook.py apps/api/tests/unit/test_platform_admin_auth_routes.py apps/api/tests/unit/test_admin_dashboard_routes.py apps/api/tests/unit/test_admin_tenants_routes.py apps/api/tests/unit/test_playground_routes.py
git commit -m "feat(api): rotas cross-tenant migram de get_session para get_system_session"
```

---

### Task 4: `apps/worker` — `open_tenant_session` e contexto de tenant nos jobs

**Files:**
- Modify: `apps/worker/app/config.py`
- Modify: `apps/worker/app/db.py`
- Modify: `apps/worker/app/tasks/messages.py`
- Modify: `apps/worker/app/tasks/knowledge_base.py`
- Test: `apps/worker/tests/unit/test_db.py` (create)
- Modify: `apps/worker/tests/unit/test_process_inbound_message.py`
- Modify: `apps/worker/tests/unit/test_ingest_knowledge_base_file.py`

**Interfaces:**
- Consumes: env `APP_DATABASE_URL` (já adicionada ao `.env.example` na Task 2, mesmo valor compartilhado com o `api`).
- Produces: `open_tenant_session(session_factory, tenant_id) -> AsyncContextManager[AsyncSession]` em `apps/worker/app/db.py` — abre a sessão e já seta `app.tenant_id`. Substitui `session_factory()` cru em todo ponto que toca tabela tenant-scoped.

- [ ] **Step 1: Renomear a env do worker**

Em `apps/worker/app/config.py`, trocar:

```python
    database_url: str
    redis_url: str
```

por:

```python
    # advoxs_app (RLS ativo) — mesmo valor de APP_DATABASE_URL usado pelo
    # api, ver migration 0008 no apps/api.
    app_database_url: str
    redis_url: str
```

- [ ] **Step 2: Escrever o teste de `open_tenant_session` que falha**

Criar `apps/worker/tests/unit/test_db.py`:

```python
from unittest.mock import AsyncMock, MagicMock

from app.db import open_tenant_session


async def test_seta_app_tenant_id_e_produz_a_sessao_do_factory() -> None:
    session = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    async with open_tenant_session(factory, "tenant-123") as yielded:
        assert yielded is session

    session.execute.assert_awaited_once()
    call = session.execute.await_args
    assert "set_config" in str(call.args[0])
    assert call.args[1] == {"tenant_id": "tenant-123"}
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `cd apps/worker && uv run pytest tests/unit/test_db.py -v`
Expected: FAIL — `ImportError: cannot import name 'open_tenant_session' from 'app.db'`.

- [ ] **Step 4: Implementar `open_tenant_session` e repontar o engine**

Substituir o conteúdo de `apps/worker/app/db.py` por:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def create_engine_and_factory() -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(settings.app_database_url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def open_tenant_session(session_factory, tenant_id: str) -> AsyncIterator[AsyncSession]:
    """Abre uma sessão e seta app.tenant_id — ativa a RLS pro papel advoxs_app.

    Mesma mecânica de get_tenant_session (apps/api/app/api/deps.py):
    set_config com is_local=true vale só pra transação atual. Todo job do
    worker já recebe tenant_id como parâmetro de entrada, então setar o
    contexto aqui é direto — sem a complicação de "resolver o tenant no
    meio do caminho" que existe nos webhooks do api.
    """
    async with session_factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        yield session
```

- [ ] **Step 5: Rodar e ver passar**

Run: `cd apps/worker && uv run pytest tests/unit/test_db.py -v`
Expected: PASS (1/1).

- [ ] **Step 6: Escrever os testes novos de `messages.py` que falham**

Em `apps/worker/tests/unit/test_process_inbound_message.py`, adicionar ao final do arquivo:

```python


async def test_load_context_seta_app_tenant_id(patched) -> None:
    ctx = _ctx()
    session = ctx["session_factory"].return_value.__aenter__.return_value

    await process_inbound_message(ctx, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    set_config_calls = [
        call
        for call in session.execute.await_args_list
        if len(call.args) > 1 and call.args[1] == {"tenant_id": TENANT_ID}
    ]
    assert len(set_config_calls) >= 1
```

- [ ] **Step 7: Rodar e ver falhar**

Run: `cd apps/worker && uv run pytest tests/unit/test_process_inbound_message.py -v -k test_load_context_seta_app_tenant_id`
Expected: FAIL — `assert 0 >= 1` (nenhuma chamada de `set_config` ainda acontece, `messages.py` ainda usa `session_factory()` cru).

- [ ] **Step 8: Atualizar `messages.py` pra usar `open_tenant_session`**

Em `apps/worker/app/tasks/messages.py`, adicionar o import (junto dos demais, após `from app.crypto import decrypt_access_token`):

```python
from app.db import open_tenant_session
```

Trocar as 3 aberturas de sessão. A primeira, dentro de `process_inbound_message`:

```python
    async with session_factory() as session:
        inbound = await _load_context(session, tenant_id, conversation_id, message_id)
```

por:

```python
    async with open_tenant_session(session_factory, tenant_id) as session:
        inbound = await _load_context(session, tenant_id, conversation_id, message_id)
```

A segunda, dentro do bloco de esgotamento de tentativas (`except httpx.HTTPError`):

```python
        async with session_factory() as session:
            await session.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == uuid.UUID(conversation_id))
                .values(state="human")
            )
            await session.commit()
        return
```

por:

```python
        async with open_tenant_session(session_factory, tenant_id) as session:
            await session.execute(
                update(tables.conversations)
                .where(tables.conversations.c.id == uuid.UUID(conversation_id))
                .values(state="human")
            )
            await session.commit()
        return
```

A terceira, no bloco final de persistência/débito:

```python
    async with session_factory() as session:
        first_message_id = await _persist_agent_responses(
            session, tenant_id, conversation_id, responses, tokens_used, credits, delivery_failures
        )
        if credits and first_message_id is not None:
            # Ledger + saldo na mesma transação das mensagens.
            await _debitar_creditos(session, tenant_id, first_message_id, tokens_used, credits)
        await session.commit()
```

por:

```python
    async with open_tenant_session(session_factory, tenant_id) as session:
        first_message_id = await _persist_agent_responses(
            session, tenant_id, conversation_id, responses, tokens_used, credits, delivery_failures
        )
        if credits and first_message_id is not None:
            # Ledger + saldo na mesma transação das mensagens.
            await _debitar_creditos(session, tenant_id, first_message_id, tokens_used, credits)
        await session.commit()
```

- [ ] **Step 9: Rodar e ver passar**

Run: `cd apps/worker && uv run pytest tests/unit/test_process_inbound_message.py -v`
Expected: PASS em todos (o teste novo do Step 6 e os já existentes — nenhum deles quebra, porque `open_tenant_session` só faz UMA chamada extra de `session.execute` além do que o código já fazia, e nenhum teste existente conta o número exato de chamadas de `session.execute`).

- [ ] **Step 10: Escrever o teste novo de `knowledge_base.py` que falha**

Em `apps/worker/tests/unit/test_ingest_knowledge_base_file.py`, adicionar ao final do arquivo:

```python


async def test_load_file_session_tem_tenant_id_setado(patched, temp_file) -> None:
    ctx = _ctx()

    await ingest_knowledge_base_file(ctx, TENANT_ID, FILE_ID)

    set_config_calls = [
        call
        for call in ctx["_session"].execute.await_args_list
        if len(call.args) > 1 and call.args[1] == {"tenant_id": TENANT_ID}
    ]
    assert len(set_config_calls) >= 1


async def test_set_status_recebe_tenant_id(patched, temp_file) -> None:
    await ingest_knowledge_base_file(_ctx(), TENANT_ID, FILE_ID)

    assert patched["set_status"].await_args.args[4] == TENANT_ID
```

- [ ] **Step 11: Rodar e ver falhar**

Run: `cd apps/worker && uv run pytest tests/unit/test_ingest_knowledge_base_file.py -v -k "test_load_file_session_tem_tenant_id_setado or test_set_status_recebe_tenant_id"`
Expected: FAIL nos dois — o primeiro porque nenhum `set_config` ainda acontece; o segundo porque `_set_status` ainda não recebe `tenant_id` (`IndexError: tuple index out of range` no `args[4]`).

- [ ] **Step 12: Atualizar `knowledge_base.py`**

Em `apps/worker/app/tasks/knowledge_base.py`, adicionar o import (junto dos demais, após `from app.config import settings`):

```python
from app.db import open_tenant_session
```

Trocar a abertura de sessão dentro de `ingest_knowledge_base_file`:

```python
    async with session_factory() as session:
        row = await _load_file(session, file_id)
```

por:

```python
    async with open_tenant_session(session_factory, tenant_id) as session:
        row = await _load_file(session, file_id)
```

Trocar os 4 call sites de `_set_status` (cada um ganha `tenant_id` como último argumento posicional, pra não deslocar os índices já usados pelos testes existentes que checam `args[2]`/`args[3]`):

```python
        await _set_status(session_factory, file_id, "error", "Arquivo temporário não encontrado")
        return
```

por:

```python
        await _set_status(
            session_factory, file_id, "error", "Arquivo temporário não encontrado", tenant_id
        )
        return
```

```python
        await _set_status(
            session_factory,
            file_id,
            "error",
            f"Falha na ingestão (HTTP {exc.response.status_code})",
        )
        return
```

por:

```python
        await _set_status(
            session_factory,
            file_id,
            "error",
            f"Falha na ingestão (HTTP {exc.response.status_code})",
            tenant_id,
        )
        return
```

```python
        await _set_status(session_factory, file_id, "error", "Serviço de ingestão indisponível")
        return
```

por:

```python
        await _set_status(
            session_factory, file_id, "error", "Serviço de ingestão indisponível", tenant_id
        )
        return
```

```python
    await _set_status(session_factory, file_id, "ready", None)
```

por:

```python
    await _set_status(session_factory, file_id, "ready", None, tenant_id)
```

Trocar a assinatura e o corpo de `_set_status`:

```python
async def _set_status(
    session_factory, file_id: str, status: str, error_message: str | None
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(tables.knowledge_base_files)
            .where(tables.knowledge_base_files.c.id == uuid.UUID(file_id))
            .values(status=status, error_message=error_message)
        )
        await session.commit()
```

por:

```python
async def _set_status(
    session_factory, file_id: str, status: str, error_message: str | None, tenant_id: str
) -> None:
    async with open_tenant_session(session_factory, tenant_id) as session:
        await session.execute(
            update(tables.knowledge_base_files)
            .where(tables.knowledge_base_files.c.id == uuid.UUID(file_id))
            .values(status=status, error_message=error_message)
        )
        await session.commit()
```

- [ ] **Step 13: Rodar e ver passar**

Run: `cd apps/worker && uv run pytest tests/unit/test_ingest_knowledge_base_file.py -v`
Expected: PASS em todos (os testes existentes que checam `args[2]`/`args[3]` continuam válidos porque `tenant_id` foi adicionado como o 5º argumento posicional, no final, sem deslocar os existentes).

- [ ] **Step 14: Rodar a suíte completa e lint**

Run: `cd apps/worker && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos PASS (o `ruff format --check` pode continuar reportando `app/worker.py` como já formatável de forma diferente — é um drift pré-existente, não relacionado a esta task, já documentado em ledgers de features anteriores; ignore especificamente esse arquivo se aparecer).

- [ ] **Step 15: Commit**

```bash
git add apps/worker/app/config.py apps/worker/app/db.py apps/worker/app/tasks/messages.py apps/worker/app/tasks/knowledge_base.py apps/worker/tests/unit/test_db.py apps/worker/tests/unit/test_process_inbound_message.py apps/worker/tests/unit/test_ingest_knowledge_base_file.py
git commit -m "feat(worker): open_tenant_session ativa RLS nos jobs (advoxs_app)"
```

---

### Task 5: Teste de integração — prova real de isolamento

**Files:**
- Create: `apps/api/tests/integration/test_rls_isolation.py`

**Interfaces:**
- Consumes: `settings.database_url` (owner, pra seed/cleanup dos dados de teste), `settings.app_database_url`, `settings.system_database_url` (Task 2).
- Produces: nenhuma interface nova — é o teste que prova que as Tasks 1-4 realmente funcionam contra um Postgres de verdade.

- [ ] **Step 1: Escrever o teste de integração**

Criar `apps/api/tests/integration/test_rls_isolation.py`:

```python
"""Prova, contra um Postgres real, que a RLS bloqueia acesso cross-tenant
pro papel advoxs_app e que advoxs_system continua vendo todos os tenants.

Só roda localmente (precisa de `docker compose up -d postgres` com a
migration 0008 aplicada) — pula com `pytest.skip` se não conseguir
conectar, não entra no CI.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()


async def _consegue_conectar(url: str) -> bool:
    try:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture
async def seeded_tenants():
    """Cria 2 tenants reais + 1 conversa cada, via papel owner (bypassa RLS)."""
    if not await _consegue_conectar(settings.database_url):
        pytest.skip("Postgres real não acessível — rode `docker compose up -d postgres` primeiro")

    owner_engine = create_async_engine(settings.database_url)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, email_contato, credit_balance) "
                "VALUES (:a, 'Tenant A (teste RLS)', 'tenant-a@teste.com', 0), "
                "(:b, 'Tenant B (teste RLS)', 'tenant-b@teste.com', 0)"
            ),
            {"a": TENANT_A, "b": TENANT_B},
        )
        await conn.execute(
            text(
                "INSERT INTO conversations (tenant_id, contact_phone_number, state) "
                "VALUES (:a, '5511900000001', 'agent'), (:b, '5511900000002', 'agent')"
            ),
            {"a": TENANT_A, "b": TENANT_B},
        )

    yield

    async with owner_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM conversations WHERE tenant_id IN (:a, :b)"),
            {"a": TENANT_A, "b": TENANT_B},
        )
        await conn.execute(
            text("DELETE FROM tenants WHERE id IN (:a, :b)"), {"a": TENANT_A, "b": TENANT_B}
        )
    await owner_engine.dispose()


class TestAdvoxsAppRoleEnforcaRLS:
    async def test_ve_so_o_proprio_tenant_sem_filtro_where(self, seeded_tenants) -> None:
        engine = create_async_engine(settings.app_database_url)
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT_A)}
            )
            result = await conn.execute(
                text(
                    "SELECT contact_phone_number FROM conversations WHERE tenant_id IN (:a, :b)"
                ),
                {"a": TENANT_A, "b": TENANT_B},
            )
            rows = [r[0] for r in result.fetchall()]
        await engine.dispose()

        assert rows == ["5511900000001"]

    async def test_insert_com_tenant_id_diferente_e_rejeitado(self, seeded_tenants) -> None:
        engine = create_async_engine(settings.app_database_url)
        with pytest.raises(DBAPIError, match="row-level security"):
            async with engine.begin() as conn:
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT_A)}
                )
                await conn.execute(
                    text(
                        "INSERT INTO conversations (tenant_id, contact_phone_number, state) "
                        "VALUES (:tenant_b, '5511900000099', 'agent')"
                    ),
                    {"tenant_b": TENANT_B},
                )
        await engine.dispose()


class TestAdvoxsSystemRoleBypassaRLS:
    async def test_ve_todos_os_tenants(self, seeded_tenants) -> None:
        engine = create_async_engine(settings.system_database_url)
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT contact_phone_number FROM conversations WHERE tenant_id IN (:a, :b) "
                    "ORDER BY contact_phone_number"
                ),
                {"a": TENANT_A, "b": TENANT_B},
            )
            rows = [r[0] for r in result.fetchall()]
        await engine.dispose()

        assert rows == ["5511900000001", "5511900000002"]
```

- [ ] **Step 2: Rodar contra o Postgres real de dev**

Run: `cd apps/api && uv run pytest tests/integration/test_rls_isolation.py -v`
Expected: PASS (3/3) — assumindo que a Task 1 já foi aplicada (`docker compose exec api uv run alembic upgrade head`) e que `.env` local tem `APP_DATABASE_URL`/`SYSTEM_DATABASE_URL` apontando pro mesmo Postgres. Se `docker compose up -d postgres` não estiver rodando, o teste pula com `pytest.skip` em vez de falhar.

- [ ] **Step 3: Confirmar que o teste pula (não falha) quando o Postgres não está acessível**

Run: `cd apps/api && DATABASE_URL="postgresql+asyncpg://usuario-inexistente:senha@localhost:1/banco-inexistente" uv run pytest tests/integration/test_rls_isolation.py -v`
Expected: `3 skipped` (nenhuma falha — confirma o comportamento de skip gracioso).

- [ ] **Step 4: Lint**

Run: `cd apps/api && uv run ruff check tests/integration/test_rls_isolation.py`
Expected: `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add apps/api/tests/integration/test_rls_isolation.py
git commit -m "test(api): prova de isolamento RLS contra Postgres real (advoxs_app/advoxs_system)"
```

---

### Task 6: `CLAUDE.md` e verificação local ponta a ponta

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Atualizar o CLAUDE.md**

Na seção "Multi-tenancy", localizar o parágrafo exato (linha única, hoje):

```markdown
- **Super-admin (plataforma)**: o painel `/admin` lê dados agregados de todos os tenants, portanto opera fora do filtro por `tenant_id`. **Hoje isso funciona porque o `api` conecta ao Postgres como `advoxs`, owner das tabelas — RLS não tem efeito sobre o owner, então as rotas do admin usam a mesma dependency de sessão simples (sem `app.tenant_id`), sem precisar de um papel `BYPASSRLS` dedicado.** Essa é uma simplificação presa ao estado atual da infraestrutura (ver pendência "RLS só tem efeito para papéis de banco que não sejam donos das tabelas" na seção "Modelo de Dados"), não uma decisão de arquitetura permanente — quando o `api` passar a conectar com um papel não-owner, as rotas do admin precisarão de um papel `BYPASSRLS` explícito ou de queries agregadas dedicadas que não setam `app.tenant_id`. A leitura de um tenant específico (não a agregada) é auditada (ver Painel de Administração da Plataforma).
```

Substituir por (mantém a frase final sobre auditoria — só troca a parte sobre o estado da RLS):

```markdown
- **Super-admin (plataforma)**: o painel `/admin` lê dados agregados de todos os tenants, portanto opera fora do filtro por `tenant_id`. ✅ **RLS efetiva em produção implementada**: o `api`/`worker` não conectam mais como owner das tabelas — três papéis de banco: `advoxs` (owner, só Alembic), `advoxs_app` (sem ownership, sem `BYPASSRLS` — rotas tenant-scoped via `get_tenant_session`/`get_session`, e jobs do `worker` via `open_tenant_session`) e `advoxs_system` (sem ownership, com `BYPASSRLS` — rotas genuinamente cross-tenant: login por e-mail, webhooks, idempotência de pagamento, e as rotas do painel de admin, via `get_system_session`). As policies `tenant_isolation` da migration `0001` agora têm efeito real pro papel `advoxs_app` — comprovado por um teste de integração dedicado (`apps/api/tests/integration/test_rls_isolation.py`) contra um Postgres real. A leitura de um tenant específico (não a agregada) continua auditada (ver Painel de Administração da Plataforma).
```

Na seção "Modelo de Dados", no bloco "Pendências do modelo de dados", remover a linha exata:

```markdown
- [ ] RLS só tem efeito para papéis de banco que não sejam donos das tabelas — produção deve conectar com um papel dedicado sem ownership/`BYPASSRLS` (hoje a aplicação conecta como owner, então as policies criadas na migration `0001` são inertes até isso).
```

O bloco "Pendências do modelo de dados" continua tendo outra linha (`- [ ] Papéis/permissões de \`users\` além de \`admin\`...`) — não fica vazio, então não precisa de tratamento especial.

Na seção "Infraestrutura (Docker Compose)", localizar o parágrafo exato (hoje uma linha só):

```markdown
- **Postgres: instância única, um database por serviço** — `advoxs` (api/worker, negócio + RLS), `advoxs_agents` (checkpoints do LangGraph) e `advoxs_rag` (metadados de documentos). Roles e databases criados por `infra/postgres/init/002-databases.sh` na primeira subida do volume; cada serviço conecta com usuário próprio e `CONNECT` revogado de `PUBLIC` (um serviço comprometido não lê o database dos outros). Senhas: `AGENTS_DB_PASSWORD`/`RAG_DB_PASSWORD` no `.env`.
```

Substituir por (troca só a descrição do database `advoxs`, mantendo o resto igual):

```markdown
- **Postgres: instância única, um database por serviço** — `advoxs` (api/worker, negócio + RLS — três papéis distintos: `advoxs` owner só pra migrations, `advoxs_app`/`advoxs_system` em runtime, ver seção Multi-tenancy e migration `0008`), `advoxs_agents` (checkpoints do LangGraph) e `advoxs_rag` (metadados de documentos). Roles e databases dos outros dois criados por `infra/postgres/init/002-databases.sh` na primeira subida do volume (os papéis do `advoxs` são criados por migration Alembic, não pelo script de init, já que precisam aplicar em bancos já existentes); cada serviço conecta com usuário próprio e `CONNECT` revogado de `PUBLIC` (um serviço comprometido não lê o database dos outros). Senhas: `AGENTS_DB_PASSWORD`/`RAG_DB_PASSWORD`/`APP_DB_PASSWORD`/`SYSTEM_DB_PASSWORD` no `.env`.
```

- [ ] **Step 2: Rotacionar o `.env` local e reiniciar os containers**

```bash
docker compose exec api uv run alembic upgrade head
```

Confirmar que `.env` local tem `APP_DB_PASSWORD`, `SYSTEM_DB_PASSWORD`, `APP_DATABASE_URL`, `SYSTEM_DATABASE_URL` setadas (mesmos valores usados durante as Tasks 1-2 de verificação manual). Depois:

```bash
docker compose up -d --build api worker
```

- [ ] **Step 3: Rodar o teste de integração de novo, agora com os containers reiniciados**

Run: `cd apps/api && uv run pytest tests/integration/test_rls_isolation.py -v`
Expected: PASS (3/3).

- [ ] **Step 4: Verificação manual ponta a ponta de cada família de rota afetada**

1. **Login** (`get_system_session`): `curl -s -X POST http://localhost:8000/api/v1/auth/login -H 'content-type: application/json' -d '{"email":"admin@demo.com","password":"segredo123"}'` — Expected: `200` com `access_token`/`refresh_token` (prova que a busca de `User` por e-mail, cross-tenant, continua funcionando via `advoxs_system`).
2. **Rota tenant-scoped normal** (`get_tenant_session`, agora via `advoxs_app`): com o token do passo 1, `curl -s http://localhost:8000/api/v1/conversations -H "Authorization: Bearer <token>"` — Expected: `200` com a lista de conversas do tenant de seed (prova que `advoxs_app` com `app.tenant_id` setado consegue ler normalmente).
3. **Painel de admin** (`get_system_session`): logar como `platform_admin` (`POST /api/v1/platform-admin/auth/login`) e chamar `GET /api/v1/platform-admin/dashboard` — Expected: `200` com números agregados de todos os tenants (prova que `advoxs_system` continua vendo tudo).
4. **Worker** (`open_tenant_session`, via `advoxs_app`): criar uma conversa + mensagem de contato reais pro tenant de seed via `psql` (mesmo padrão já usado em features anteriores), disparar `process_inbound_message` manualmente dentro do container `worker` (`uv run python3`, chamando a função direto, sem passar por Redis/Arq real) — Expected: a mensagem do agente é persistida corretamente (confirma via `psql` que a linha foi inserida com o `tenant_id` certo — se `app.tenant_id` não estivesse setado, o `INSERT` teria sido **rejeitado pelo Postgres**, então o job teria estourado uma exceção em vez de completar silenciosamente).
5. Limpar os dados de teste criados no passo 4 (conversa, mensagens) e restaurar qualquer estado alterado.

Expected: todos os passos funcionam; o passo 4 é o mais importante — prova que o `worker`, que nunca setava `app.tenant_id` antes desta feature, agora grava com sucesso porque o contexto de tenant está correto (e provaria uma regressão real, não silenciosa, se estivesse errado).

- [ ] **Step 5: Rodar as suítes completas de `api` e `worker` uma última vez**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -q && uv run ruff check . && uv run ruff format --check .`
Run: `cd apps/worker && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: tudo PASS/limpo (exceto o drift pré-existente e já conhecido de `apps/worker/app/worker.py` no `ruff format --check`, não relacionado a esta feature).

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: RLS efetivo em produção documentado no CLAUDE.md"
```
