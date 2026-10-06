"""Cadastro pendente para confirmação de e-mail antes do pagamento.

Revision ID: 0043
Revises: 0042
"""

import sqlalchemy as sa

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_signups",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("tenant_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String()),
        sa.Column(
            "credit_package_id", sa.Uuid(), sa.ForeignKey("credit_packages.id"), nullable=False
        ),
        sa.Column("verification_token_hash", sa.String(64)),
        sa.Column("verification_expires_at", sa.DateTime(timezone=True)),
        sa.Column("checkout_token_hash", sa.String(64)),
        sa.Column("checkout_expires_at", sa.DateTime(timezone=True)),
        sa.Column("stripe_checkout_id", sa.String()),
        sa.Column("stripe_checkout_url", sa.String()),
        sa.Column("stripe_checkout_created_at", sa.DateTime(timezone=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_sent_at", sa.DateTime(timezone=True)),
        sa.Column("send_window_started_at", sa.DateTime(timezone=True)),
        sa.Column("send_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_pending_signups_verification_token_hash",
        "pending_signups",
        ["verification_token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_pending_signups_checkout_token_hash",
        "pending_signups",
        ["checkout_token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_pending_signups_checkout_token_hash", table_name="pending_signups")
    op.drop_index("ix_pending_signups_verification_token_hash", table_name="pending_signups")
    op.drop_table("pending_signups")
