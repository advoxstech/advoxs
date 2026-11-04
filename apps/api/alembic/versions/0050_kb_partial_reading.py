"""Partial reading of knowledge base files (pages without text or low-quality OCR).

Revision ID: 0050
Revises: 0049
"""

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE knowledge_base_files "
        "ADD COLUMN pages_total integer, "
        "ADD COLUMN partial_pages integer[]"
    )


def downgrade():
    op.execute(
        "ALTER TABLE knowledge_base_files DROP COLUMN partial_pages, DROP COLUMN pages_total"
    )
