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
        CheckConstraint("type IN ('purchase', 'consumption', 'refund', 'bonus')", name="type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    # Positivo em purchase/bonus, negativo em consumption.
    amount_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    related_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("messages.id"))
    credit_package_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("credit_packages.id")
    )
    stripe_payment_id: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
