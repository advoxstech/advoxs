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
