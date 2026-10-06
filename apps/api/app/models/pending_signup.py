"""Cadastro aguardando confirmação de e-mail e pagamento."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PendingSignup(Base):
    __tablename__ = "pending_signups"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    tenant_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String)
    credit_package_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("credit_packages.id"), nullable=False
    )
    verification_token_hash: Mapped[str | None] = mapped_column(String(64))
    verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkout_token_hash: Mapped[str | None] = mapped_column(String(64))
    checkout_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stripe_checkout_id: Mapped[str | None] = mapped_column(String)
    stripe_checkout_url: Mapped[str | None] = mapped_column(String)
    stripe_checkout_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    send_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    send_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
