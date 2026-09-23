import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UsageRecord(Base):
    """Custo operacional e cobrança efetiva de uma execução do agente."""

    __tablename__ = "usage_records"
    __table_args__ = (
        CheckConstraint(
            "funding_source IN ('tenant', 'end_customer_credits', 'end_customer_subscription')",
            name="funding_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id"), nullable=False
    )
    related_message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("messages.id"), nullable=False, unique=True
    )
    contact_phone_number: Mapped[str] = mapped_column(String, nullable=False)
    funding_source: Mapped[str] = mapped_column(String, nullable=False)
    operational_credits: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    billed_credits: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, server_default=text("0")
    )
    shortfall_credits: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, server_default=text("0")
    )
    document_credits: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, server_default=text("0")
    )
    tokens_input: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tokens_output: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    pricing_config_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pricing_configs.id")
    )
    end_customer_subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("end_customer_subscriptions.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
