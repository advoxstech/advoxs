import uuid

from pydantic import BaseModel


class BillingBalanceOut(BaseModel):
    credit_balance: float


class BillingCheckoutRequest(BaseModel):
    credit_package_id: uuid.UUID


class BillingCheckoutUrlOut(BaseModel):
    checkout_url: str


class BillingStatusOut(BaseModel):
    ready: bool
