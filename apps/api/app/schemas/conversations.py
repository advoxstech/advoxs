import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.contacts import TagOut


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_phone_number: str
    contact_id: uuid.UUID | None = None
    contact_name: str | None = None
    human_only: bool = False
    tags: list[TagOut] = Field(default_factory=list)
    state: Literal["agent", "human", "billing_gate"]
    status: Literal["agent", "human", "billing_gate", "processing", "failed"] = "agent"
    is_test: bool
    last_message_at: datetime | None
    last_message_preview: str | None = None
    last_message_media_type: str | None = None
    last_message_sender_type: Literal["agent", "human", "contact", "system"] | None = None
    created_at: datetime
    summary: str | None
    summary_generated_at: datetime | None
    end_customer_balance: float | None = None
    end_customer_cycle_total: float | None = None
    end_customer_cycle_consumed: float | None = None
    end_customer_billing_exempt: bool = False
    end_customer_billing_enabled: bool = False
    current_agent_name: str | None = None
    assigned_user_id: uuid.UUID | None = None
    assigned_user_name: str | None = None
    urgent_since: datetime | None = None
    urgent_reason: str | None = None
    urgent_source: Literal["agent", "keyword", "manual"] | None = None
    urgent_agent_name: str | None = None


class ResponseSource(BaseModel):
    document_id: uuid.UUID
    filename: str
    chunk_id: str
    excerpt: str
    page: int | None = None
    # Última página do trecho, quando ele cruza páginas (senão igual a page).
    page_end: int | None = None
    # Páginas do documento com leitura parcial (sem texto ou OCR fraco).
    partial_pages: list[int] = Field(default_factory=list)


class ResponseSources(BaseModel):
    status: Literal[
        "referenced", "no_documents", "not_searched", "empty", "not_used", "unavailable"
    ]
    sources: list[ResponseSource] = Field(default_factory=list)
    search_failed: bool = False


class MessageMediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: Literal["audio", "document", "image"]
    status: Literal["pending", "processing", "done", "failed", "skipped"]
    error_code: str | None = None
    text: str | None = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sender_type: Literal["agent", "human", "contact", "system"]
    sender_user_id: uuid.UUID | None = None
    sender_user_name: str | None = None
    content: str
    media_url: str | None
    media_type: str | None
    media: MessageMediaOut | None = None
    response_sources: ResponseSources | None = None
    delivery_status: Literal["pending", "sent", "failed", "cancelled"] | None
    created_at: datetime


class ConversationStateUpdate(BaseModel):
    state: Literal["agent", "human"]


class BillingExemptionUpdate(BaseModel):
    exempt: bool


class UrgencyUpdate(BaseModel):
    urgent: bool


class UrgentCountOut(BaseModel):
    count: int


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)


class TestMessagesOut(BaseModel):
    messages: list[MessageOut]
    grouped: bool
    # Documento grande ainda em leitura: a resposta do agente chega depois.
    reading: bool = False


class ConversationUsageOut(BaseModel):
    conversation_id: uuid.UUID
    contact_phone_number: str
    is_test: bool
    credits_consumed: float
    billed_responses: int
    last_message_at: datetime
