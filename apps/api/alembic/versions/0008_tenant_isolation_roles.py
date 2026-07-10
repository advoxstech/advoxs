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
