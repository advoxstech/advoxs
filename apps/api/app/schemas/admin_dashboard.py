import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class TenantsByStatus(BaseModel):
    active: int
    suspended: int


class NewTenantsPerDay(BaseModel):
    day: date
    count: int


class CreditsSummary(BaseModel):
    sold: float
    consumed: float


class LowBalanceTenant(BaseModel):
    id: uuid.UUID
    name: str
    credit_balance: float


class WhatsappConnectedSummary(BaseModel):
    connected: int
    total: int


class KnowledgeBaseUsageSummary(BaseModel):
    total_files: int
    total_size_bytes: int


class PendingZApiRequest(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    requested_at: datetime


class AdminDashboardOut(BaseModel):
    tenants_total: int
    tenants_by_status: TenantsByStatus
    new_tenants_last_30_days: list[NewTenantsPerDay]
    revenue_brl_last_30_days: Decimal
    credits_summary: CreditsSummary
    messages_processed: int
    agent_executions: int
    tokens_consumed: int
    openai_cost_estimate_usd: Decimal
    low_balance_tenants: list[LowBalanceTenant]
    whatsapp_connected: WhatsappConnectedSummary
    knowledge_base_usage: KnowledgeBaseUsageSummary
    pending_zapi_requests: list[PendingZApiRequest] = Field(default_factory=list)
