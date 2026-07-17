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
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TenantBillingSettings(Base):
    """Configuração da cobrança do cliente final (tenant-scoped, 1:1 com tenant).

    `billing_mode` só suporta "credits" por ora — hook de extensibilidade
    para modos futuros (assinatura, por conversa).
    """

    __tablename__ = "tenant_billing_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, unique=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    billing_mode: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'credits'")
    )
    # Único valor suportado por ora — hook de extensibilidade (como billing_mode).
    insufficient_balance_policy: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'block_with_message'")
    )
    stripe_secret_key_encrypted: Mapped[str | None] = mapped_column(Text)
    stripe_webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    end_customer_tokens_per_credit: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class EndCustomerCreditPackage(Base):
    """Pacote de créditos que o tenant vende aos próprios clientes finais."""

    __tablename__ = "end_customer_credit_packages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    price_brl: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    credits_granted: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class EndCustomerBalance(Base):
    """Saldo de créditos de um cliente final com um tenant específico."""

    __tablename__ = "end_customer_balances"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    contact_phone_number: Mapped[str] = mapped_column(String, nullable=False)
    credit_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class EndCustomerCreditTransaction(Base):
    """Ledger do saldo do cliente final (tenant-scoped) — purchase/consumption."""

    __tablename__ = "end_customer_credit_transactions"
    __table_args__ = (
        CheckConstraint(
            "type IN ('purchase', 'consumption', 'resale', 'adjustment')", name="type"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    contact_phone_number: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    amount_credits: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # Auditoria de consumo: tokens brutos + config de pricing vigente no débito.
    tokens_input: Mapped[int | None] = mapped_column(Integer)
    tokens_output: Mapped[int | None] = mapped_column(Integer)
    pricing_config_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pricing_configs.id")
    )
    end_customer_credit_package_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("end_customer_credit_packages.id")
    )
    related_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("messages.id"))
    stripe_payment_id: Mapped[str | None] = mapped_column(String, unique=True)
    description: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
