"""seed dos pacotes de créditos (Starter/Growth/Scale/Enterprise)

Dado de referência que precisa existir de forma idêntica em qualquer
ambiente — cadastro self-service depende desses 4 pacotes existirem.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-08
"""

import uuid

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

credit_packages = sa.table(
    "credit_packages",
    sa.column("id", sa.Uuid()),
    sa.column("name", sa.String()),
    sa.column("price_brl", sa.Numeric(10, 2)),
    sa.column("credits_granted", sa.Integer()),
    sa.column("active", sa.Boolean()),
)

PACKAGES = [
    {"name": "Starter", "price_brl": "100.00", "credits_granted": 1000},
    {"name": "Growth", "price_brl": "250.00", "credits_granted": 2750},
    {"name": "Scale", "price_brl": "500.00", "credits_granted": 6000},
    {"name": "Enterprise", "price_brl": "1000.00", "credits_granted": 13000},
]


def upgrade() -> None:
    op.bulk_insert(
        credit_packages,
        [
            {
                "id": uuid.uuid4(),
                "name": p["name"],
                "price_brl": p["price_brl"],
                "credits_granted": p["credits_granted"],
                "active": True,
            }
            for p in PACKAGES
        ],
    )


def downgrade() -> None:
    op.execute(
        credit_packages.delete().where(credit_packages.c.name.in_([p["name"] for p in PACKAGES]))
    )
