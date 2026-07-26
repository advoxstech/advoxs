import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TenantBillingSettingsOut(BaseModel):
    tenant_id: uuid.UUID
    enabled: bool
    billing_mode: str
    billing_provider: str
    stripe_account_id: str | None
    stripe_account_status: str | None
    stripe_secret_key_configured: bool
    stripe_webhook_secret_configured: bool
    end_customer_tokens_per_credit: int | None
    # URL completa (com domínio) — montada no backend a partir de
    # settings.api_public_url, nunca no client (NEXT_PUBLIC_* do Next.js só é
    # embutido em build-time, e o build de produção não recebe essa env).
    webhook_url: str


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
    kind: str
    credits_granted: int | None
    active: bool


class EndCustomerCreditPackageIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    price_brl: Decimal = Field(gt=0)
    kind: str = Field(default="one_time")
    credits_granted: int | None = Field(default=None, gt=0)
    active: bool = True

    @model_validator(mode="after")
    def _valida_kind_e_credits_granted(self) -> "EndCustomerCreditPackageIn":
        if self.kind not in ("one_time", "subscription"):
            raise ValueError("kind deve ser 'one_time' ou 'subscription'")
        if self.kind == "one_time" and self.credits_granted is None:
            raise ValueError("credits_granted é obrigatório para pacotes avulsos (kind=one_time)")
        if self.kind == "subscription":
            # kind é autoritativo — um pacote de assinatura nunca tem
            # credits_granted (acesso ilimitado, não medido em créditos).
            # Normaliza em vez de rejeitar.
            self.credits_granted = None
        return self


class EndCustomerCreditPackageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price_brl: Decimal | None = Field(default=None, gt=0)
    credits_granted: int | None = Field(default=None, gt=0)
    active: bool | None = None


class InternalCheckoutRequest(BaseModel):
    tenant_id: uuid.UUID
    contact_phone_number: str = Field(min_length=1)
    package_id: uuid.UUID


class CheckoutUrlOut(BaseModel):
    checkout_url: str


class EndCustomerSummaryOut(BaseModel):
    contact_phone_number: str
    credit_balance: float
    total_purchased: float
    total_consumed: float


class ConnectAccountSessionOut(BaseModel):
    client_secret: str
