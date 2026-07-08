"""Definições Core das tabelas que o worker acessa.

O schema é dono do `apps/api` (models + migrations Alembic); aqui só as
colunas usadas pelos jobs. Manter em sincronia com apps/api/app/models/.
"""

from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, String, Table, Text, Uuid, text

metadata = MetaData()

tenants = Table(
    "tenants",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("credit_balance", Integer),
)

conversations = Table(
    "conversations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("contact_phone_number", String),
    Column("state", String),
    Column("last_message_at", DateTime(timezone=True)),
)

messages = Table(
    "messages",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("conversation_id", Uuid),
    Column("tenant_id", Uuid),
    Column("sender_type", String),
    Column("content", Text),
    Column("tokens_used", Integer),
    Column("credits_consumed", Numeric(12, 2)),
    Column("created_at", DateTime(timezone=True), server_default=text("now()")),
)

credit_transactions = Table(
    "credit_transactions",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("tenant_id", Uuid),
    Column("type", String),
    Column("amount_credits", Integer),
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
