import uuid
from decimal import Decimal

from app.tasks import messages as messages_task

TENANT_ID = str(uuid.uuid4())
CONTACT = "5511999998888"
MESSAGE_ID = uuid.uuid4()
PRICING_CONFIG_ID = uuid.uuid4()


class FakeSession:
    """Captura os params de cada statement executado (SELECT do lock incluso)."""

    def __init__(self):
        self.executed: list[dict] = []

    async def execute(self, stmt):
        params = dict(stmt.compile().params)
        self.executed.append(params)

    def insert_params(self) -> dict:
        return next(p for p in self.executed if "amount_credits" in p)


async def test_lanca_consumption_negativo_e_atualiza_saldo() -> None:
    session = FakeSession()

    await messages_task._debitar_creditos_cliente_final(
        session,
        TENANT_ID,
        CONTACT,
        MESSAGE_ID,
        tokens_used=2000,
        credits=Decimal("2.0000"),
        tokens_input=1400,
        tokens_output=600,
        pricing_config_id=PRICING_CONFIG_ID,
    )

    # lock (SELECT FOR UPDATE) + insert no ledger + update relativo do saldo
    assert len(session.executed) == 3
    transaction = session.insert_params()
    assert transaction["type"] == "consumption"
    assert transaction["amount_credits"] == Decimal("-2.0000")
    assert transaction["contact_phone_number"] == CONTACT
    assert transaction["related_message_id"] == MESSAGE_ID
    assert transaction["tokens_input"] == 1400
    assert transaction["tokens_output"] == 600
    assert transaction["pricing_config_id"] == PRICING_CONFIG_ID
    assert "token" not in transaction["description"].lower()


async def test_debito_do_tenant_grava_tokens_brutos_e_config() -> None:
    session = FakeSession()

    await messages_task._debitar_creditos(
        session,
        TENANT_ID,
        MESSAGE_ID,
        tokens_used=2000,
        credits=Decimal("1.0200"),
        tokens_input=1400,
        tokens_output=600,
        pricing_config_id=PRICING_CONFIG_ID,
    )

    assert len(session.executed) == 3
    transaction = session.insert_params()
    assert transaction["type"] == "consumption"
    assert transaction["amount_credits"] == Decimal("-1.0200")
    assert transaction["tokens_input"] == 1400
    assert transaction["tokens_output"] == 600
    assert transaction["pricing_config_id"] == PRICING_CONFIG_ID
    assert "token" not in transaction["description"].lower()


async def test_sem_breakdown_grava_null_em_tokens() -> None:
    # Auditoria é opcional: agents antigo (sem breakdown) manda 0 -> NULL.
    session = FakeSession()

    await messages_task._debitar_creditos(
        session, TENANT_ID, MESSAGE_ID, tokens_used=2000, credits=Decimal("2.0000")
    )

    transaction = session.insert_params()
    assert transaction["tokens_input"] is None
    assert transaction["tokens_output"] is None
    assert transaction["pricing_config_id"] is None
