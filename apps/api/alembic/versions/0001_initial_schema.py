"""estado inicial: tabelas do modelo de dados + RLS

Revision ID: 0001
Revises:
Create Date: 2026-07-07
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Tabelas com tenant_id — RLS aplicado como camada extra de proteção, além do
# filtro na aplicação. A policy só tem efeito para papéis de banco que não sejam
# donos da tabela (produção deve usar um papel dedicado sem ownership/BYPASSRLS).
TENANT_SCOPED_TABLES = [
    "users",
    "whatsapp_numbers",
    "knowledge_base_files",
    "conversations",
    "messages",
    "credit_transactions",
]


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("cnpj", sa.String(), nullable=True),
        sa.Column("email_contato", sa.String(), nullable=False),
        sa.Column("credit_balance", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.String(), server_default=sa.text("'active'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('active', 'suspended')", name=op.f("ck_tenants_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
        sa.UniqueConstraint("cnpj", name=op.f("uq_tenants_cnpj")),
    )

    op.create_table(
        "platform_admins",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), server_default=sa.text("'superadmin'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform_admins")),
        sa.UniqueConstraint("email", name=op.f("uq_platform_admins_email")),
    )

    op.create_table(
        "credit_packages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("price_brl", sa.Numeric(10, 2), nullable=False),
        sa.Column("credits_granted", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credit_packages")),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), server_default=sa.text("'admin'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_users_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_tenant_id"), "users", ["tenant_id"])

    op.create_table(
        "whatsapp_numbers",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("phone_number_id", sa.String(), nullable=False),
        sa.Column("waba_id", sa.String(), nullable=False),
        sa.Column("display_phone_number", sa.String(), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), server_default=sa.text("'connected'"), nullable=False),
        sa.Column(
            "connected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('connected', 'disconnected')", name=op.f("ck_whatsapp_numbers_status")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_whatsapp_numbers_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_whatsapp_numbers")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_whatsapp_numbers_tenant_id")),
        sa.UniqueConstraint("phone_number_id", name=op.f("uq_whatsapp_numbers_phone_number_id")),
    )

    op.create_table(
        "knowledge_base_files",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default=sa.text("'processing'"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('processing', 'ready', 'error')",
            name=op.f("ck_knowledge_base_files_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_knowledge_base_files_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_base_files")),
    )
    op.create_index(
        op.f("ix_knowledge_base_files_tenant_id"), "knowledge_base_files", ["tenant_id"]
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_phone_number", sa.String(), nullable=False),
        sa.Column("state", sa.String(), server_default=sa.text("'agent'"), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("state IN ('agent', 'human')", name=op.f("ck_conversations_state")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_conversations_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.UniqueConstraint(
            "tenant_id", "contact_phone_number", name=op.f("uq_conversations_tenant_id")
        ),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("sender_type", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("media_url", sa.String(), nullable=True),
        sa.Column("media_type", sa.String(), nullable=True),
        sa.Column("wa_message_id", sa.String(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("credits_consumed", sa.Numeric(12, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sender_type IN ('agent', 'human', 'contact')", name=op.f("ck_messages_sender_type")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_messages_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint("wa_message_id", name=op.f("uq_messages_wa_message_id")),
    )
    op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"])
    op.create_index("ix_messages_tenant_id_created_at", "messages", ["tenant_id", "created_at"])

    op.create_table(
        "credit_transactions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("amount_credits", sa.Integer(), nullable=False),
        sa.Column("related_message_id", sa.Uuid(), nullable=True),
        sa.Column("credit_package_id", sa.Uuid(), nullable=True),
        sa.Column("stripe_payment_id", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "type IN ('purchase', 'consumption', 'refund', 'bonus')",
            name=op.f("ck_credit_transactions_type"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_credit_transactions_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["related_message_id"],
            ["messages.id"],
            name=op.f("fk_credit_transactions_related_message_id_messages"),
        ),
        sa.ForeignKeyConstraint(
            ["credit_package_id"],
            ["credit_packages.id"],
            name=op.f("fk_credit_transactions_credit_package_id_credit_packages"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credit_transactions")),
    )
    op.create_index(op.f("ix_credit_transactions_tenant_id"), "credit_transactions", ["tenant_id"])

    # RLS: policy filtra por app.tenant_id (setado pela aplicação a cada request).
    # current_setting(..., true) retorna NULL quando não setado -> nenhuma linha.
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("credit_transactions")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("knowledge_base_files")
    op.drop_table("whatsapp_numbers")
    op.drop_table("users")
    op.drop_table("credit_packages")
    op.drop_table("platform_admins")
    op.drop_table("tenants")
