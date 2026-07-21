import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
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


class SubscriptionPlan(Base):
    """Plano de assinatura mensal (global) — define tetos de agentes, base de
    conhecimento e ferramentas extras (reservado, sem enforcement ainda),
    mais um bônus de créditos concedido a cada ciclo pago. `NULL` num teto
    significa sem limite — usado só pelo plano "Legado" (nunca vendido,
    migra tenants já existentes sem regressão)."""

    __tablename__ = "subscription_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    price_brl: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    max_agents: Mapped[int | None] = mapped_column(Integer)
    max_extra_tools: Mapped[int | None] = mapped_column(Integer)
    max_knowledge_base_files: Mapped[int | None] = mapped_column(Integer)
    max_knowledge_base_storage_bytes: Mapped[int | None] = mapped_column(BigInteger)
    monthly_credits_granted: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    is_legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class TenantSubscription(Base):
    """Assinatura vigente de um tenant (tenant-scoped, 1:1) — aponta pro
    plano atual e espelha o status/ciclo da assinatura no Stripe.
    `stripe_subscription_id` é `NULL` só pra tenants no plano Legado (sem
    assinatura Stripe de verdade — Postgres permite múltiplos `NULL` numa
    coluna `UNIQUE`, então isso nunca colide entre tenants)."""

    __tablename__ = "tenant_subscriptions"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'past_due', 'canceled')", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, unique=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("subscription_plans.id"), nullable=False
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'active'"))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
