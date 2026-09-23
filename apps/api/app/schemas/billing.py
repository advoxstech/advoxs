import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BillingBalanceOut(BaseModel):
    credit_balance: float


class BillingCheckoutRequest(BaseModel):
    credit_package_id: uuid.UUID


class BillingCheckoutUrlOut(BaseModel):
    checkout_url: str


class BillingStatusOut(BaseModel):
    ready: bool


class BillingTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    amount_credits: float
    description: str | None
    created_at: datetime


class SpendingByMonthOut(BaseModel):
    month: str
    total_brl: float


class SpendingReportOut(BaseModel):
    by_month: list[SpendingByMonthOut]


class UsageSummaryOut(BaseModel):
    executions: int
    operational_credits: float
    billed_credits: float
    shortfall_credits: float
    subscription_credits: float
    tenant_credits: float
    end_customer_credits: float
    document_credits: float
    tokens_input: int
    tokens_output: int


class UsageRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_phone_number: str
    funding_source: str
    operational_credits: float
    billed_credits: float
    shortfall_credits: float
    document_credits: float
    tokens_input: int
    tokens_output: int
    created_at: datetime


class UsageReportOut(BaseModel):
    summary: UsageSummaryOut
    items: list[UsageRecordOut]
