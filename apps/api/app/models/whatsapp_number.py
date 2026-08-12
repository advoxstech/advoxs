import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WhatsAppNumber(Base):
    """Número WhatsApp conectado (tenant-scoped, 1:1 com tenant) — via Meta
    (oficial) ou Z-API (não-oficial, conexão por QR code, sem aprovação de
    negócio). `provider` discrimina qual bloco de colunas está preenchido."""

    __tablename__ = "whatsapp_numbers"
    __table_args__ = (
        CheckConstraint("status IN ('connected', 'disconnected')", name="status"),
        CheckConstraint(
            "(provider = 'meta' AND phone_number_id IS NOT NULL AND waba_id IS NOT NULL "
            "AND access_token_encrypted IS NOT NULL) "
            "OR (provider = 'zapi' AND zapi_instance_id IS NOT NULL "
            "AND zapi_instance_token_encrypted IS NOT NULL AND zapi_webhook_secret IS NOT NULL)",
            name="ck_whatsapp_numbers_provider_fields",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, unique=True
    )
    provider: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'meta'"))
    # Meta — nullable, só preenchido quando provider="meta".
    # Unique: é a chave de resolução do webhook (phone_number_id -> tenant);
    # dois tenants nunca compartilham o mesmo número.
    phone_number_id: Mapped[str | None] = mapped_column(String, unique=True)
    waba_id: Mapped[str | None] = mapped_column(String)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    # Z-API — nullable, só preenchido quando provider="zapi".
    # zapi_instance_id é a chave de resolução do webhook, equivalente ao
    # phone_number_id da Meta.
    zapi_instance_id: Mapped[str | None] = mapped_column(String, unique=True)
    zapi_instance_token_encrypted: Mapped[str | None] = mapped_column(Text)
    zapi_client_token_encrypted: Mapped[str | None] = mapped_column(Text)
    # Segredo nosso (não é credencial da Z-API) que compõe a URL do webhook —
    # única camada de autenticação do endpoint, já que a Z-API não assina o
    # payload como a Meta faz.
    zapi_webhook_secret: Mapped[str | None] = mapped_column(String)
    # True quando a instância foi criada manualmente por um funcionário da
    # Advoxs (fora do Programa de Parceiro/Integrador da Z-API, que exigiria
    # R$899/mês) e atribuída a este tenant via painel de admin — o tenant
    # nunca vê instance_id/token, só escaneia o QR code. False (default) pra
    # quem conectou a própria conta Z-API via self-service.
    zapi_managed_by_advoxs: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    display_phone_number: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'connected'"))
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
