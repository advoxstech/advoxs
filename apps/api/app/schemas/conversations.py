import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_phone_number: str
    state: Literal["agent", "human"]
    last_message_at: datetime | None
    created_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sender_type: Literal["agent", "human", "contact"]
    content: str
    media_url: str | None
    media_type: str | None
    created_at: datetime


class ConversationStateUpdate(BaseModel):
    state: Literal["agent", "human"]


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)
