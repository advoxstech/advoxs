"""esatdo inicial

Revision ID: 9428acb91486
Revises:
Create Date: 2026-06-11 22:44:56.438342

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9428acb91486"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    Originalmente vazia (o schema era criado pelo create_all do startup),
    o que quebrava `alembic upgrade head` em banco novo. Preenchida com o
    estado das tabelas na época desta revisão — bancos já stampados nesta
    revisão não re-executam este passo.
    """
    op.create_table(
        "documentos_usuario",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("nome", sa.String(), nullable=False),
        sa.Column("extensao", sa.String(), nullable=False),
        sa.Column("path_base", sa.String(), nullable=False),
        sa.Column("path_doc", sa.String(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "documentos_sistema",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("base", sa.String(), nullable=True),
        sa.Column("nome", sa.String(), nullable=False),
        sa.Column("extensao", sa.String(), nullable=False),
        sa.Column("path_base", sa.String(), nullable=False),
        sa.Column("path_doc", sa.String(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("documentos_sistema")
    op.drop_table("documentos_usuario")
