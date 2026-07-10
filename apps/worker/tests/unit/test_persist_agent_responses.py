import uuid

from app.tasks import messages as messages_task

TENANT_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class FakeSession:
    def __init__(self):
        self.executed_values: list[dict] = []
        self.next_id = uuid.uuid4()

    async def execute(self, stmt):
        params = dict(stmt.compile().params)
        self.executed_values.append(params)
        if "sender_type" in params:
            return FakeResult(self.next_id)
        return FakeResult(None)


async def test_marca_delivery_status_sent_por_padrao() -> None:
    session = FakeSession()

    first_id = await messages_task._persist_agent_responses(
        session, TENANT_ID, CONVERSATION_ID, ["resposta 1", "resposta 2"], 100, 1
    )

    assert first_id == session.next_id
    inserted = [v for v in session.executed_values if "sender_type" in v]
    assert inserted[0]["delivery_status"] == "sent"
    assert inserted[1]["delivery_status"] == "sent"


async def test_marca_delivery_status_failed_pelo_indice() -> None:
    session = FakeSession()

    await messages_task._persist_agent_responses(
        session, TENANT_ID, CONVERSATION_ID, ["resposta 1", "resposta 2"], 100, 1, {1}
    )

    inserted = [v for v in session.executed_values if "sender_type" in v]
    assert inserted[0]["delivery_status"] == "sent"
    assert inserted[1]["delivery_status"] == "failed"
