import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, Uuid
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MessageMedia(Base):
    """Estado do processamento de mídia de uma mensagem (migration 0052).

    Escrita só pelo worker (transcrição de áudio; OCR na frente do PDF).
    O painel apenas lê `kind`/`status`/`error_code`/`text`."""

    __tablename__ = "message_media"

    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    error_code: Mapped[str | None] = mapped_column(String(32))
    text: Mapped[str | None] = mapped_column(Text)
    uncertain_spans: Mapped[list | None] = mapped_column(JSONB)
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    provider: Mapped[str | None] = mapped_column(String(64))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    credits_consumed: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
