"""Media processing state (audio transcription, OCR) and transcription pricing.

Revision ID: 0052
Revises: 0051
"""

from pathlib import Path

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text().split("\n-- statement\n"):
        op.execute(statement)


def downgrade():
    # A linha de pricing_configs inserida no upgrade fica: lançamentos de
    # transcrição podem referenciá-la (credit_transactions.pricing_config_id).
    op.execute("ALTER TABLE pricing_configs DROP COLUMN transcription_credits_per_minute")
    op.execute("DROP TABLE message_media")
