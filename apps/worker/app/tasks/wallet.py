"""Débitos de wallet (tenant e cliente final), compartilhados pelo turno do agente
e pela transcrição de áudio."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import tables


async def _debitar_creditos(
    session: AsyncSession,
    tenant_id: str,
    message_id: uuid.UUID,
    tokens_used: int,
    credits: Decimal,
    tokens_input: int = 0,
    tokens_output: int = 0,
    pricing_config_id: uuid.UUID | None = None,
    description: str = "Consumo do agente",
) -> tuple[bool, Decimal]:
    """Lança o consumo no ledger e atualiza o cache de saldo do tenant.

    O SELECT ... FOR UPDATE serializa débitos concorrentes do mesmo tenant
    (várias mensagens simultâneas) — o update relativo em seguida nunca perde
    escrita nem lê saldo obsoleto.

    Devolve se este débito zerou o saldo e quanto foi efetivamente cobrado.
    O valor cobrado nunca ultrapassa o saldo travado, portanto concorrência
    não produz saldo negativo.

    True representa a transição de positivo para zero — usado pra notificar
    a Advoxs uma única vez por
    "episódio" de saldo esgotado, nunca a cada mensagem enquanto já
    está zerado (ver send_tenant_out_of_credits_notification)."""
    saldo_antes = (
        await session.execute(
            select(tables.tenants.c.credit_balance)
            .where(tables.tenants.c.id == uuid.UUID(tenant_id))
            .with_for_update()
        )
    ).scalar_one()
    saldo_disponivel = max(Decimal(0), saldo_antes)
    credits_cobrados = min(saldo_disponivel, credits)
    if credits_cobrados:
        await session.execute(
            insert(tables.credit_transactions).values(
                tenant_id=uuid.UUID(tenant_id),
                type="consumption",
                amount_credits=-credits_cobrados,
                related_message_id=message_id,
                tokens_input=tokens_input or None,
                tokens_output=tokens_output or None,
                pricing_config_id=pricing_config_id,
                description=description,
                created_at=datetime.now(UTC),
            )
        )
        await session.execute(
            update(tables.tenants)
            .where(tables.tenants.c.id == uuid.UUID(tenant_id))
            .values(credit_balance=tables.tenants.c.credit_balance - credits_cobrados)
        )
    return saldo_antes > 0 and credits_cobrados == saldo_disponivel, credits_cobrados


async def _debitar_creditos_cliente_final(
    session: AsyncSession,
    tenant_id: str,
    contact_phone_number: str,
    message_id: uuid.UUID,
    tokens_used: int,
    credits: Decimal,
    tokens_input: int = 0,
    tokens_output: int = 0,
    pricing_config_id: uuid.UUID | None = None,
    description: str = "Consumo do agente",
) -> Decimal:
    """Débito do saldo do CLIENTE FINAL com o tenant — moeda única: quando o
    turno é custeado pelo cliente, SÓ esta wallet é debitada (o estoque do
    tenant já foi debitado na revenda). FOR UPDATE serializa débitos
    concorrentes do mesmo contato."""
    saldo_antes = (
        await session.execute(
            select(tables.end_customer_balances.c.credit_balance)
            .where(
                tables.end_customer_balances.c.tenant_id == uuid.UUID(tenant_id),
                tables.end_customer_balances.c.contact_phone_number == contact_phone_number,
            )
            .with_for_update()
        )
    ).scalar_one()
    saldo_disponivel = max(Decimal(0), saldo_antes)
    credits_cobrados = min(saldo_disponivel, credits)
    if not credits_cobrados:
        return Decimal(0)
    await session.execute(
        insert(tables.end_customer_credit_transactions).values(
            tenant_id=uuid.UUID(tenant_id),
            contact_phone_number=contact_phone_number,
            type="consumption",
            amount_credits=-credits_cobrados,
            related_message_id=message_id,
            tokens_input=tokens_input or None,
            tokens_output=tokens_output or None,
            pricing_config_id=pricing_config_id,
            description=description,
            created_at=datetime.now(UTC),
        )
    )
    await session.execute(
        update(tables.end_customer_balances)
        .where(
            tables.end_customer_balances.c.tenant_id == uuid.UUID(tenant_id),
            tables.end_customer_balances.c.contact_phone_number == contact_phone_number,
        )
        .values(credit_balance=tables.end_customer_balances.c.credit_balance - credits_cobrados)
    )
    return credits_cobrados
