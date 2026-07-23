"""`InboundContext` isolado num módulo próprio, sem nenhuma outra dependência
de `app.tasks.messages` ou `app.billing_gate` — ambos precisam do tipo (um
pra montá-lo, o outro só pra anotação de tipo) e um import direto entre eles
formaria um ciclo (`app.tasks.messages` -> `app.billing_gate` -> de volta
pra `app.tasks.messages` pelo `InboundContext`). Continua reexportado por
`app.tasks.messages.InboundContext` pra não quebrar nenhum import existente."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class InboundContext:
    conversation_state: str
    contact_phone_number: str
    message_content: str
    phone_number_id: str
    access_token_encrypted: str
    credit_balance: Decimal
    end_customer_billing_enabled: bool
    end_customer_balance: Decimal
    end_customer_packages: list[dict]
    agents: list[dict]
    human_last_seen_at: datetime | None = None
    billing_gate_step: str | None = None
    billing_gate_retries: int = 0
    billing_gate_checkout_url: str | None = None
    billing_gate_welcome_text: str | None = None
    end_customer_billing_exempt: bool = False
