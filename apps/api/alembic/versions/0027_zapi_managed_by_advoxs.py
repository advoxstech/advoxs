"""whatsapp_numbers.zapi_managed_by_advoxs — fluxo manual de Z-API gerenciado
pela Advoxs (sem Programa de Parceiro/Integrador da Z-API, que exigiria
mínimo de R$899/mês).

Uma instância Z-API criada manualmente por um funcionário da Advoxs (no
painel da própria Z-API) e atribuída a um tenant via
POST /platform-admin/tenants/{id}/whatsapp/zapi grava True aqui — o painel do
tenant usa essa flag pra pular o formulário de credenciais e ir direto pro
QR code (o tenant nunca vê nem digita instance_id/token). Instâncias que o
próprio tenant conectou via self-service (POST /whatsapp/connect-zapi)
continuam com False, o default — nenhuma mudança de comportamento pra quem
já usa Z-API hoje.

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-12
"""

import sqlalchemy as sa

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_numbers",
        sa.Column(
            "zapi_managed_by_advoxs",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_numbers", "zapi_managed_by_advoxs")
