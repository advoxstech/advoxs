import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class TenantBillingSettingsOut(BaseModel):
    enabled: bool
    billing_mode: str
    stripe_secret_key_configured: bool
    stripe_webhook_secret_configured: bool
    end_customer_tokens_per_credit: int | None


class TenantBillingSettingsUpdate(BaseModel):
    """PATCH parcial — campos omitidos mantêm o valor já salvo.

    `stripe_secret_key`/`stripe_webhook_secret` omitidos não sobrescrevem o
    valor cifrado existente (evita ter que reenviar a secret key a cada PATCH
    de outro campo, ex: só ligar o toggle `enabled`).
    """

    enabled: bool | None = None
    stripe_secret_key: str | None = Field(default=None, min_length=1)
    stripe_webhook_secret: str | None = Field(default=None, min_length=1)
    end_customer_tokens_per_credit: int | None = Field(default=None, gt=0)


class EndCustomerCreditPackageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    price_brl: Decimal
    credits_granted: int
    active: bool


class EndCustomerCreditPackageIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    price_brl: Decimal = Field(gt=0)
    credits_granted: int = Field(gt=0)
    active: bool = True


class EndCustomerCreditPackageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price_brl: Decimal | None = Field(default=None, gt=0)
    credits_granted: int | None = Field(default=None, gt=0)
    active: bool | None = None
