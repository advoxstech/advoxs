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


def _conversation(**overrides):
    row = SimpleNamespace(
        state="agent",
        contact_phone_number="5511999998888",
        human_last_seen_at=None,
        billing_gate_step=None,
        billing_gate_retries=0,
        billing_gate_checkout_url=None,
        end_customer_billing_exempt=False,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


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
    billing_settings = SimpleNamespace(
        enabled=True, insufficient_balance_policy="block_with_message", billing_gate_welcome_text=None
    )
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
    billing_settings = SimpleNamespace(
        enabled=True, insufficient_balance_policy="block_with_message", billing_gate_welcome_text=None
    )
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


async def test_carrega_campos_do_billing_gate_da_conversa() -> None:
    conversation = _conversation(
        billing_gate_step="aguardando_pagamento",
        billing_gate_retries=2,
        billing_gate_checkout_url="https://checkout.stripe.com/xyz",
    )
    session = _session_with(
        conversation=conversation,
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=None,
        balance=None,
        packages=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.billing_gate_step == "aguardando_pagamento"
    assert context.billing_gate_retries == 2
    assert context.billing_gate_checkout_url == "https://checkout.stripe.com/xyz"


async def test_carrega_policy_e_texto_de_boas_vindas_do_tenant() -> None:
    billing_settings = SimpleNamespace(
        enabled=True,
        insufficient_balance_policy="deterministic_gate",
        billing_gate_welcome_text="Bem-vindo ao nosso escritório!",
    )
    session = _session_with(
        conversation=_conversation(),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=billing_settings,
        balance=0,
        packages=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.insufficient_balance_policy == "deterministic_gate"
    assert context.billing_gate_welcome_text == "Bem-vindo ao nosso escritório!"


async def test_sem_billing_settings_usa_policy_default() -> None:
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

    assert context.insufficient_balance_policy == "block_with_message"
    assert context.billing_gate_welcome_text is None


async def test_carrega_isencao_de_cobranca_da_conversa() -> None:
    session = _session_with(
        conversation=_conversation(end_customer_billing_exempt=True),
        content="Olá",
        number=_number(),
        credit_balance=1000,
        billing_settings=None,
        balance=None,
        packages=[],
    )

    context = await _load_context(session, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert context.end_customer_billing_exempt is True


async def test_isencao_default_e_false() -> None:
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

    assert context.end_customer_billing_exempt is False
