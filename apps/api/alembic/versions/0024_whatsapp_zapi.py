"""Adiciona suporte a Z-API como provedor alternativo de WhatsApp — coluna
discriminadora `provider` + campos zapi_* em whatsapp_numbers, mesmo padrão
já usado em tenant_billing_settings.billing_provider. Ver
docs/superpowers/specs/2026-07-29-whatsapp-zapi-design.md.

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-29
"""

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_numbers",
        sa.Column("provider", sa.String(), nullable=False, server_default="meta"),
    )
    op.alter_column("whatsapp_numbers", "phone_number_id", nullable=True)
    op.alter_column("whatsapp_numbers", "waba_id", nullable=True)
    op.alter_column("whatsapp_numbers", "access_token_encrypted", nullable=True)

    op.add_column("whatsapp_numbers", sa.Column("zapi_instance_id", sa.String(), nullable=True))
    op.create_unique_constraint(
        "uq_whatsapp_numbers_zapi_instance_id", "whatsapp_numbers", ["zapi_instance_id"]
    )
    op.add_column(
        "whatsapp_numbers", sa.Column("zapi_instance_token_encrypted", sa.Text(), nullable=True)
    )
    op.add_column(
        "whatsapp_numbers", sa.Column("zapi_client_token_encrypted", sa.Text(), nullable=True)
    )
    op.add_column("whatsapp_numbers", sa.Column("zapi_webhook_secret", sa.String(), nullable=True))

    op.create_check_constraint(
        "ck_whatsapp_numbers_provider_fields",
        "whatsapp_numbers",
        "(provider = 'meta' AND phone_number_id IS NOT NULL AND waba_id IS NOT NULL "
        "AND access_token_encrypted IS NOT NULL) "
        "OR (provider = 'zapi' AND zapi_instance_id IS NOT NULL "
        "AND zapi_instance_token_encrypted IS NOT NULL AND zapi_webhook_secret IS NOT NULL)",
    )


def downgrade() -> None:
    # As colunas Meta (phone_number_id, waba_id, access_token_encrypted)
    # voltam a NOT NULL no final desta função — isso falha se sobrar
    # qualquer linha provider='zapi' (esses campos ficam NULL nela por
    # desenho, ver ck_whatsapp_numbers_provider_fields). O downgrade assume
    # que nenhum tenant real segue conectado via Z-API no momento do
    # rollback; apagar essas linhas aqui é mais seguro operacionalmente do
    # que deixar o downgrade quebrar no meio (constraint parcialmente
    # revertida) — o tenant afetado simplesmente perde a conexão e precisa
    # reconectar (via Meta ou Z-API de novo, depois de um upgrade seguinte).
    op.execute("DELETE FROM whatsapp_numbers WHERE provider = 'zapi'")
    op.drop_constraint("ck_whatsapp_numbers_provider_fields", "whatsapp_numbers", type_="check")
    op.drop_column("whatsapp_numbers", "zapi_webhook_secret")
    op.drop_column("whatsapp_numbers", "zapi_client_token_encrypted")
    op.drop_column("whatsapp_numbers", "zapi_instance_token_encrypted")
    op.drop_constraint("uq_whatsapp_numbers_zapi_instance_id", "whatsapp_numbers", type_="unique")
    op.drop_column("whatsapp_numbers", "zapi_instance_id")
    op.alter_column("whatsapp_numbers", "access_token_encrypted", nullable=False)
    op.alter_column("whatsapp_numbers", "waba_id", nullable=False)
    op.alter_column("whatsapp_numbers", "phone_number_id", nullable=False)
    op.drop_column("whatsapp_numbers", "provider")
