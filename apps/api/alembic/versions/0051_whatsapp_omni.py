"""WhatsApp oficial via Z-API Omni: terceiro provedor em whatsapp_numbers.

O workspace do Omni é da Advoxs (uma Secret Key da plataforma, no .env); cada
escritório é um canal. Por número guardamos só o canal, o token do canal e o
webhook que criamos nele.

Revision ID: 0051
Revises: 0050
"""

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

# Nome com que a 0024 criou a regra (a convenção de nomes duplica o prefixo);
# mantido igual para continuar batendo com o modelo SQLAlchemy.
_NOME_REGRA = "ck_whatsapp_numbers_ck_whatsapp_numbers_provider_fields"
_REGRA_ANTIGA = (
    "(provider = 'meta' AND phone_number_id IS NOT NULL AND waba_id IS NOT NULL "
    "AND access_token_encrypted IS NOT NULL) "
    "OR (provider = 'zapi' AND zapi_instance_id IS NOT NULL "
    "AND zapi_instance_token_encrypted IS NOT NULL AND zapi_webhook_secret IS NOT NULL)"
)
_REGRA_NOVA = (
    _REGRA_ANTIGA + " OR (provider = 'omni' AND omni_channel_id IS NOT NULL "
    "AND omni_channel_token_encrypted IS NOT NULL AND omni_webhook_path_secret IS NOT NULL)"
)


def upgrade():
    op.execute(
        "ALTER TABLE whatsapp_numbers "
        "ADD COLUMN omni_channel_id varchar, "
        "ADD COLUMN omni_channel_token_encrypted text, "
        "ADD COLUMN omni_webhook_id varchar, "
        "ADD COLUMN omni_webhook_signing_secret_encrypted text, "
        "ADD COLUMN omni_webhook_path_secret varchar, "
        "ADD CONSTRAINT uq_whatsapp_numbers_omni_channel_id UNIQUE (omni_channel_id), "
        "ADD CONSTRAINT uq_whatsapp_numbers_omni_webhook_path_secret "
        "UNIQUE (omni_webhook_path_secret)"
    )
    _drop_checks("whatsapp_numbers", "zapi_instance_id")
    op.execute(f"ALTER TABLE whatsapp_numbers ADD CONSTRAINT {_NOME_REGRA} CHECK ({_REGRA_NOVA})")
    # Follow-up: no Omni, como na Meta, só modelo aprovado. As regras da 0049
    # não têm nome explícito; troca as duas que citam o provedor.
    _drop_checks("follow_up_batches", "provider")
    op.execute(
        "ALTER TABLE follow_up_batches "
        "ADD CONSTRAINT ck_follow_up_batches_provider "
        "CHECK (provider IN ('meta','zapi','omni')), "
        "ADD CONSTRAINT ck_follow_up_batches_content "
        "CHECK ((provider IN ('meta','omni') AND template IS NOT NULL) "
        "OR (provider = 'zapi' AND text IS NOT NULL))"
    )


def _drop_checks(table: str, trecho: str):
    """Remove as regras CHECK da tabela cujo texto cita `trecho`.

    Localiza pelo conteúdo, não pelo nome: a convenção de nomes do projeto
    duplica o prefixo (ck_whatsapp_numbers_ck_whatsapp_numbers_provider_fields)
    e as regras da 0049 têm nome gerado pelo Postgres."""
    op.execute(
        f"""
        DO $$
        DECLARE c record;
        BEGIN
          FOR c IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = '{table}'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) LIKE '%{trecho}%'
          LOOP
            EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', c.conname);
          END LOOP;
        END $$;
        """
    )


def downgrade():
    op.execute("DELETE FROM follow_up_batches WHERE provider = 'omni'")
    _drop_checks("follow_up_batches", "provider")
    op.execute(
        "ALTER TABLE follow_up_batches "
        "ADD CONSTRAINT follow_up_batches_provider_check CHECK (provider IN ('meta','zapi')), "
        "ADD CONSTRAINT follow_up_batches_check "
        "CHECK ((provider = 'meta' AND template IS NOT NULL) "
        "OR (provider = 'zapi' AND text IS NOT NULL))"
    )
    # Como no downgrade da 0024: um número Omni não cabe na regra antiga; o
    # escritório perde a conexão e precisa reconectar depois de um novo upgrade.
    op.execute("DELETE FROM whatsapp_numbers WHERE provider = 'omni'")
    _drop_checks("whatsapp_numbers", "zapi_instance_id")
    op.execute(f"ALTER TABLE whatsapp_numbers ADD CONSTRAINT {_NOME_REGRA} CHECK ({_REGRA_ANTIGA})")
    op.execute(
        "ALTER TABLE whatsapp_numbers "
        "DROP COLUMN omni_webhook_path_secret, "
        "DROP COLUMN omni_webhook_signing_secret_encrypted, "
        "DROP COLUMN omni_webhook_id, "
        "DROP COLUMN omni_channel_token_encrypted, "
        "DROP COLUMN omni_channel_id"
    )
