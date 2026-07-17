import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


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


class AdminDashboardOut(BaseModel):
    tenants_total: int
    tenants_by_status: TenantsByStatus
    new_tenants_last_30_days: list[NewTenantsPerDay]
    revenue_brl_last_30_days: Decimal
    credits_summary: CreditsSummary
    messages_processed: int
    agent_executions: int
    tokens_consumed: int
    low_balance_tenants: list[LowBalanceTenant]
    whatsapp_connected: WhatsappConnectedSummary
    knowledge_base_usage: KnowledgeBaseUsageSummary
