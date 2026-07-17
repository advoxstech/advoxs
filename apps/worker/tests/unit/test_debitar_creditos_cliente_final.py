import uuid

from app.tasks import messages as messages_task

TENANT_ID = str(uuid.uuid4())
CONTACT = "5511999998888"
MESSAGE_ID = uuid.uuid4()


class FakeSession:
    def __init__(self):
        self.executed: list[dict] = []

    async def execute(self, stmt):
        params = dict(stmt.compile().params)
        self.executed.append(params)


async def test_lanca_consumption_negativo_e_atualiza_saldo() -> None:
    session = FakeSession()

    await messages_task._debitar_creditos_cliente_final(
        session,
        TENANT_ID,
        CONTACT,
        MESSAGE_ID,
        tokens_used=2000,
        credits=4,
        tokens_input=1400,
        tokens_output=600,
    )

    transaction = session.executed[0]
    assert transaction["type"] == "consumption"
    assert transaction["amount_credits"] == -4
    assert transaction["contact_phone_number"] == CONTACT
    assert transaction["related_message_id"] == MESSAGE_ID
    assert transaction["tokens_input"] == 1400
    assert transaction["tokens_output"] == 600


async def test_debito_do_tenant_grava_tokens_brutos() -> None:
    session = FakeSession()

    await messages_task._debitar_creditos(
        session,
        TENANT_ID,
        MESSAGE_ID,
        tokens_used=2000,
        credits=2,
        tokens_input=1400,
        tokens_output=600,
    )

    transaction = session.executed[0]
    assert transaction["type"] == "consumption"
    assert transaction["amount_credits"] == -2
    assert transaction["tokens_input"] == 1400
    assert transaction["tokens_output"] == 600


async def test_sem_breakdown_grava_null_em_tokens() -> None:
    # Auditoria é opcional: agents antigo (sem breakdown) manda 0 -> NULL.
    session = FakeSession()

    await messages_task._debitar_creditos(
        session, TENANT_ID, MESSAGE_ID, tokens_used=2000, credits=2
    )

    transaction = session.executed[0]
    assert transaction["tokens_input"] is None
    assert transaction["tokens_output"] is None
