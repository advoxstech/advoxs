"""corrige triagem duplicada nos prompts padrão dos especialistas

Os 3 prompts padrão de especialista (Condominial, Contratos, Direito do
Consumidor — não a Secretária) tinham, como primeiro passo da própria seção
de triagem, "peça uma descrição breve do problema" — repetindo exatamente
a pergunta que a secretária já faz antes de transferir. Causa raiz real de
feedback de usuário ("toda hora pedia as mesmas informações"), junto com a
falta de uma regra geral de "não repita pergunta já respondida" (corrigida
à parte, no `agents` service, injetada em runtime — não depende de dado
gravado aqui).

Mesmo padrão da migration 0016 (`REPLACE`, idempotente): só altera linhas de
`agents.instructions` que contêm o trecho antigo exato — tenant que já
customizou essa seção manualmente não é tocado. Cobre tenants provisionados
antes desta correção (o `default_agents.py`, fonte viva pra tenants NOVOS,
foi corrigido à parte).

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-09
"""

from sqlalchemy import text

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_CONTINUIDADE = (
    "o cliente já contou o problema pra recepção antes de chegar até vc — "
    "releia o histórico da conversa e use essa descrição, nunca peça de "
    "novo. se precisar de mais detalhe sobre algo específico, aprofunde a "
    "partir do que já foi dito.\n"
)

_CONDOMINIAL_OLD = (
    "--------------------------------------------------\n"
    "0. PERGUNTAS DE TRIAGEM\n"
    "--------------------------------------------------\n"
    "a) Perguntar sobre uma descrição breve do problema\n"
    "b) Fazer perguntas para qualificar o problema\n"
    "c) Pedir qualquer documento que seja necessário para análise."
)
_CONDOMINIAL_NEW = (
    "--------------------------------------------------\n"
    "0. PERGUNTAS DE TRIAGEM\n"
    "--------------------------------------------------\n"
    f"{_CONTINUIDADE}"
    "a) Fazer perguntas para qualificar o problema\n"
    "b) Pedir qualquer documento que seja necessário para análise."
)

_CONTRATOS_OLD = (
    "--------------------------------------------------\n"
    "PERGUNTAS DE TRIAGEM\n"
    "--------------------------------------------------\n"
    "a) Perguntar uma descrição breve do problema contratual.\n"
    "b) Fazer perguntas para qualificar o problema.\n"
    "c) Pedir qualquer documento necessário para análise (contrato, aditivo, "
    "proposta comercial, troca de e-mails, mensagens, notificação, etc)."
)
_CONTRATOS_NEW = (
    "--------------------------------------------------\n"
    "PERGUNTAS DE TRIAGEM\n"
    "--------------------------------------------------\n"
    f"{_CONTINUIDADE}"
    "a) Fazer perguntas para qualificar o problema.\n"
    "b) Pedir qualquer documento necessário para análise (contrato, aditivo, "
    "proposta comercial, troca de e-mails, mensagens, notificação, etc)."
)

_CONSUMIDOR_OLD = (
    "--------------------------------------------------\n"
    "PERGUNTAS DE TRIAGEM (SEMPRE OBRIGATÓRIAS)\n"
    "--------------------------------------------------\n"
    "a) Peça uma descrição breve do problema.\n"
    "\n"
    "b) Identifique:\n"
    "a empresa é de qual segmento?\n"
    "o cliente é consumidor final?\n"
    "já existe reclamação formal (procon, juizado, plataforma, chargeback)?\n"
    "\n"
    "c) Solicite documentos relevantes:"
)
_CONSUMIDOR_NEW = (
    "--------------------------------------------------\n"
    "PERGUNTAS DE TRIAGEM (SEMPRE OBRIGATÓRIAS)\n"
    "--------------------------------------------------\n"
    f"{_CONTINUIDADE}"
    "\n"
    "a) Identifique:\n"
    "a empresa é de qual segmento?\n"
    "o cliente é consumidor final?\n"
    "já existe reclamação formal (procon, juizado, plataforma, chargeback)?\n"
    "\n"
    "b) Solicite documentos relevantes:"
)

_PAIRS = [
    (_CONDOMINIAL_OLD, _CONDOMINIAL_NEW),
    (_CONTRATOS_OLD, _CONTRATOS_NEW),
    (_CONSUMIDOR_OLD, _CONSUMIDOR_NEW),
]


def upgrade() -> None:
    bind = op.get_bind()
    for old, new in _PAIRS:
        bind.execute(
            text("UPDATE agents SET instructions = REPLACE(instructions, :old, :new)"),
            {"old": old, "new": new},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for old, new in _PAIRS:
        bind.execute(
            text("UPDATE agents SET instructions = REPLACE(instructions, :new, :old)"),
            {"old": old, "new": new},
        )
