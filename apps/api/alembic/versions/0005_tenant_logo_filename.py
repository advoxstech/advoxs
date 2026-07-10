"""logo_filename em tenants

Nome do arquivo de logo salvo no volume logo_uploads — nullable, tenant
sem logo enviada ainda não tem essa coluna preenchida.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-09
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("logo_filename", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "logo_filename")
