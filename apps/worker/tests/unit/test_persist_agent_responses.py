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
        session, TENANT_ID, CONVERSATION_ID, ["resposta 1", "resposta 2"], None, 100, 1
    )

    assert first_id == session.next_id
    inserted = [v for v in session.executed_values if "sender_type" in v]
    assert inserted[0]["delivery_status"] == "sent"
    assert inserted[1]["delivery_status"] == "sent"


async def test_marca_delivery_status_failed_pelo_indice() -> None:
    session = FakeSession()

    await messages_task._persist_agent_responses(
        session, TENANT_ID, CONVERSATION_ID, ["resposta 1", "resposta 2"], None, 100, 1, {1}
    )

    inserted = [v for v in session.executed_values if "sender_type" in v]
    assert inserted[0]["delivery_status"] == "sent"
    assert inserted[1]["delivery_status"] == "failed"


async def test_documento_gerado_persiste_mensagem_com_media_url() -> None:
    session = FakeSession()
    documents = [
        {"link": "https://agents.exemplo.com/generated-documents/doc-1", "filename": "Multa.pdf"}
    ]

    await messages_task._persist_agent_responses(
        session, TENANT_ID, CONVERSATION_ID, ["segue a multa"], documents, 100, 21
    )

    inserted = [v for v in session.executed_values if "sender_type" in v]
    assert len(inserted) == 2
    doc_row = inserted[1]
    assert doc_row["media_url"] == documents[0]["link"]
    assert doc_row["media_type"] == "application/pdf"
    assert "Multa.pdf" in doc_row["content"]


async def test_documento_marca_delivery_status_pelo_campo_delivered() -> None:
    session = FakeSession()
    documents = [
        {"link": "https://exemplo.com/a", "filename": "A.pdf", "delivered": True},
        {"link": "https://exemplo.com/b", "filename": "B.pdf", "delivered": False},
    ]

    await messages_task._persist_agent_responses(
        session, TENANT_ID, CONVERSATION_ID, [], documents, 0, 40
    )

    inserted = [v for v in session.executed_values if "sender_type" in v]
    assert inserted[0]["delivery_status"] == "sent"
    assert inserted[1]["delivery_status"] == "failed"


async def test_sem_texto_credito_vai_pro_primeiro_documento() -> None:
    """Sem nenhuma resposta de texto, o custo da execução (tokens + custo
    fixo de documento) fica registrado na primeira mensagem que existir —
    que passa a ser o documento."""
    session = FakeSession()
    documents = [{"link": "https://exemplo.com/a", "filename": "A.pdf"}]

    first_id = await messages_task._persist_agent_responses(
        session, TENANT_ID, CONVERSATION_ID, [], documents, 100, 20
    )

    assert first_id == session.next_id
    inserted = [v for v in session.executed_values if "sender_type" in v]
    assert inserted[0]["tokens_used"] == 100
    assert inserted[0]["credits_consumed"] == 20
