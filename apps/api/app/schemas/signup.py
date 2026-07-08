import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CreditPackageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    price_brl: Decimal
    credits_granted: int


class SignupCheckoutRequest(BaseModel):
    tenant_name: str = Field(min_length=1)
    email: EmailStr
    password: str = Field(min_length=8)
    credit_package_id: uuid.UUID


class CheckoutUrlOut(BaseModel):
    checkout_url: str


class SignupStatusOut(BaseModel):
    ready: bool
