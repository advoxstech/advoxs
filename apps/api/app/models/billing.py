import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PricingConfig(Base):
    """Config global de pricing, versionada (não tenant-scoped).

    Nunca editar uma linha existente: mudança de pesos/proporção cria uma
    linha nova com `effective_at`; cada lançamento de consumo grava a config
    vigente no momento (auditoria/recalculabilidade do histórico).
    """

    __tablename__ = "pricing_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tokens_per_credit: Mapped[int] = mapped_column(Integer, nullable=False)
    input_weight: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    output_weight: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CreditPackage(Base):
    """Pacote de créditos à venda (global)."""

    __tablename__ = "credit_packages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    price_brl: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    credits_granted: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class CreditTransaction(Base):
    """Ledger de créditos (tenant-scoped) — fonte da verdade do saldo."""

    __tablename__ = "credit_transactions"
    __table_args__ = (
        CheckConstraint(
            "type IN ('purchase', 'consumption', 'refund', 'bonus', 'resale', 'adjustment')",
            name="type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    # Positivo em purchase/bonus, negativo em consumption/resale (saída do estoque).
    amount_credits: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # Auditoria de consumo: tokens brutos + config de pricing vigente no débito.
    tokens_input: Mapped[int | None] = mapped_column(Integer)
    tokens_output: Mapped[int | None] = mapped_column(Integer)
    pricing_config_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pricing_configs.id")
    )
    related_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("messages.id"))
    credit_package_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("credit_packages.id")
    )
    stripe_payment_id: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
