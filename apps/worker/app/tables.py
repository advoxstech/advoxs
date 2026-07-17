"""Definições Core das tabelas que o worker acessa.

O schema é dono do `apps/api` (models + migrations Alembic); aqui só as
colunas usadas pelos jobs. Manter em sincronia com apps/api/app/models/.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    Uuid,
    text,
)

metadata = MetaData()

pricing_configs = Table(
    "pricing_configs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tokens_per_credit", Integer),
    Column("input_weight", Numeric(6, 4)),
    Column("output_weight", Numeric(6, 4)),
    Column("effective_at", DateTime(timezone=True)),
)

tenants = Table(
    "tenants",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("credit_balance", Numeric(12, 4)),
)

conversations = Table(
    "conversations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("contact_phone_number", String),
    Column("state", String),
    Column("is_test", Boolean, nullable=False),
    Column("last_message_at", DateTime(timezone=True)),
    Column("human_last_seen_at", DateTime(timezone=True)),
)

messages = Table(
    "messages",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("conversation_id", Uuid),
    Column("tenant_id", Uuid),
    Column("sender_type", String),
    Column("content", Text),
    Column("delivery_status", String),
    Column("tokens_used", Integer),
    Column("credits_consumed", Numeric(12, 4)),
    Column("created_at", DateTime(timezone=True), server_default=text("now()")),
)

credit_transactions = Table(
    "credit_transactions",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("tenant_id", Uuid),
    Column("type", String),
    Column("amount_credits", Numeric(12, 4)),
    Column("tokens_input", Integer),
    Column("tokens_output", Integer),
    Column("pricing_config_id", Uuid),
    Column("related_message_id", Uuid),
    Column("description", String),
    Column("created_at", DateTime(timezone=True), server_default=text("now()")),
)

whatsapp_numbers = Table(
    "whatsapp_numbers",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("phone_number_id", String),
    Column("access_token_encrypted", Text),
    Column("status", String),
)

knowledge_base_files = Table(
    "knowledge_base_files",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("filename", String),
    Column("status", String),
    Column("error_message", Text),
)

tenant_billing_settings = Table(
    "tenant_billing_settings",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("enabled", Boolean),
    Column("end_customer_tokens_per_credit", Integer),
)

end_customer_credit_packages = Table(
    "end_customer_credit_packages",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("name", String),
    Column("price_brl", Numeric(10, 2)),
    Column("credits_granted", Integer),
    Column("active", Boolean),
)

end_customer_balances = Table(
    "end_customer_balances",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("contact_phone_number", String),
    Column("credit_balance", Numeric(12, 4)),
)

end_customer_credit_transactions = Table(
    "end_customer_credit_transactions",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("tenant_id", Uuid),
    Column("contact_phone_number", String),
    Column("type", String),
    Column("amount_credits", Numeric(12, 4)),
    Column("tokens_input", Integer),
    Column("tokens_output", Integer),
    Column("pricing_config_id", Uuid),
    Column("related_message_id", Uuid),
    Column("description", String),
    Column("created_at", DateTime(timezone=True), server_default=text("now()")),
)
