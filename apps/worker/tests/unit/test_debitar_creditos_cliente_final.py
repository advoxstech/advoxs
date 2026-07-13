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
        session, TENANT_ID, CONTACT, MESSAGE_ID, tokens_used=2000, credits=4
    )

    transaction = session.executed[0]
    assert transaction["type"] == "consumption"
    assert transaction["amount_credits"] == -4
    assert transaction["contact_phone_number"] == CONTACT
    assert transaction["related_message_id"] == MESSAGE_ID
