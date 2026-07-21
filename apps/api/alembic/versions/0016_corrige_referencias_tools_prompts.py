"""corrige referências a tools antigas nas instruções de agentes já provisionados

A Etapa 2 (motor dinâmico de agentes) renomeou transfer_to_specialist ->
transfer_to_agent e buscar_base_conhecimento_escritorio ->
buscar_base_conhecimento_agente. Agentes provisionados pela migration 0015
(backfill) ou por signups/seeds anteriores a este deploy têm essas strings
antigas cravadas em agents.instructions (texto livre editável pelo tenant,
não código) — corrige via REPLACE, idempotente e sem tocar linhas que
nunca mencionaram essas tools.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-20
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE agents SET instructions = "
        "REPLACE(REPLACE(instructions, "
        "'transfer_to_specialist', 'transfer_to_agent'), "
        "'buscar_base_conhecimento_escritorio', 'buscar_base_conhecimento_agente')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE agents SET instructions = "
        "REPLACE(REPLACE(instructions, "
        "'transfer_to_agent', 'transfer_to_specialist'), "
        "'buscar_base_conhecimento_agente', 'buscar_base_conhecimento_escritorio')"
    )
