import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TenantListItemOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    credit_balance: int
    created_at: datetime
    whatsapp_connected: bool


class CreditTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    amount_credits: int
    description: str | None
    created_at: datetime


class KnowledgeBaseFileSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    status: str
    uploaded_at: datetime


class TenantDetailOut(BaseModel):
    id: uuid.UUID
    name: str
    email_contato: str
    status: str
    credit_balance: int
    created_at: datetime
    recent_transactions: list[CreditTransactionOut]
    knowledge_base_files: list[KnowledgeBaseFileSummaryOut]
