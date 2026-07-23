import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_phone_number: str
    state: Literal["agent", "human", "billing_gate"]
    is_test: bool
    last_message_at: datetime | None
    created_at: datetime
    summary: str | None
    summary_generated_at: datetime | None
    end_customer_balance: float | None = None
    end_customer_cycle_total: float | None = None
    end_customer_cycle_consumed: float | None = None
    end_customer_billing_exempt: bool = False
    end_customer_billing_enabled: bool = False


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sender_type: Literal["agent", "human", "contact", "system"]
    content: str
    media_url: str | None
    media_type: str | None
    delivery_status: Literal["sent", "failed"] | None
    created_at: datetime


class ConversationStateUpdate(BaseModel):
    state: Literal["agent", "human"]


class BillingExemptionUpdate(BaseModel):
    exempt: bool


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)


class TestMessagesOut(BaseModel):
    messages: list[MessageOut]
    grouped: bool


class ConversationUsageOut(BaseModel):
    conversation_id: uuid.UUID
    contact_phone_number: str
    is_test: bool
    credits_consumed: float
    billed_responses: int
    last_message_at: datetime
