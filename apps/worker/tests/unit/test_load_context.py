import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.tasks.messages import _load_context

TENANT_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())
MESSAGE_ID = str(uuid.uuid4())


def _session_with(
    conversation,
    content,
    number,
    credit_balance,
    billing_settings,
    balance,
    packages,
    agents_rows=None,
    agent_kb_links=None,
):
    session = AsyncMock()

    def _result(value=None, scalar=None, rows=None):
        result = MagicMock()
        result.one_or_none.return_value = value
        result.scalar_one_or_none.return_value = scalar
        result.scalar_one.return_value = scalar
        result.all.return_value = rows or []
        result.__iter__ = lambda self: iter(rows or [])
        return result

    session.execute = AsyncMock(
        side_effect=[
            _result(value=conversation),
            _result(scalar=content),
            _result(value=number),
            _result(scalar=credit_balance),
            _result(value=billing_settings),
            _result(rows=agents_rows),
            _result(rows=agent_kb_links),
            _result(scalar=balance),
            _result(rows=packages),
        ]
    )
    return session


def _conversation():
    return SimpleNamespace(
        state="agent", contact_phone_number="5511999998888", human_last_seen_at=None
    )


def _number():
    return SimpleNamespace(phone_number_id="PNID", access_token_encrypted="cifrado")


async def test_billing_desabilitado_retorna_saldo_zero_e_sem_pacotes() -> None:
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=None,
        balance=None,
        packages=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.end_customer_billing_enabled is False
    assert context.end_customer_balance == 0
    assert context.end_customer_packages == []


async def test_billing_habilitado_le_saldo_e_pacotes() -> None:
    billing_settings = SimpleNamespace(enabled=True)
    package_row = SimpleNamespace(
        id=uuid.uuid4(), name="Básico", price_brl=49.9, credits_granted=500
    )
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=billing_settings,
        balance=250,
        packages=[package_row],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.end_customer_billing_enabled is True
    assert context.end_customer_balance == 250
    assert context.end_customer_packages == [
        {
            "id": str(package_row.id),
            "name": "Básico",
            "price_brl": "49.9",
            "credits_granted": 500,
        }
    ]


async def test_billing_habilitado_sem_saldo_ainda_usa_zero() -> None:
    billing_settings = SimpleNamespace(enabled=True)
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=billing_settings,
        balance=None,
        packages=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.end_customer_balance == 0


async def test_carrega_agentes_do_tenant_com_arquivos_anexados() -> None:
    agent_id = uuid.uuid4()
    other_agent_id = uuid.uuid4()
    file_id = uuid.uuid4()
    agent_row = SimpleNamespace(
        id=agent_id, name="Secretária", instructions="instruções", is_entry_point=True
    )
    other_row = SimpleNamespace(
        id=other_agent_id,
        name="Condominial",
        instructions="outras instruções",
        is_entry_point=False,
    )
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=None,
        balance=None,
        packages=[],
        agents_rows=[agent_row, other_row],
        agent_kb_links=[(agent_id, file_id)],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.agents == [
        {
            "id": str(agent_id),
            "name": "Secretária",
            "instructions": "instruções",
            "is_entry_point": True,
            "knowledge_base_file_ids": [str(file_id)],
        },
        {
            "id": str(other_agent_id),
            "name": "Condominial",
            "instructions": "outras instruções",
            "is_entry_point": False,
            "knowledge_base_file_ids": [],
        },
    ]


async def test_sem_agentes_retorna_lista_vazia() -> None:
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=None,
        balance=None,
        packages=[],
        agents_rows=[],
        agent_kb_links=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.agents == []
    assert session.execute.await_count == 7
