import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WhatsappStatusOut(BaseModel):
    connected: bool
    display_phone_number: str | None


class ConversationsSummaryOut(BaseModel):
    total: int
    waiting_human: int


class UsageSummaryOut(BaseModel):
    agent_messages: int
    credits_consumed: float


class KnowledgeBaseSummaryOut(BaseModel):
    ready: int
    error: int


class RecentConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_phone_number: str
    state: str
    last_message_at: datetime | None


class TenantDashboardOut(BaseModel):
    credit_balance: float
    whatsapp: WhatsappStatusOut
    conversations: ConversationsSummaryOut
    usage_last_30_days: UsageSummaryOut
    knowledge_base: KnowledgeBaseSummaryOut
    recent_conversations: list[RecentConversationOut]
