import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from arq.worker import Retry
from cryptography.fernet import Fernet

from app.config import settings
from app.crypto import decrypt_access_token
from app.tasks import messages as messages_task
from app.tasks.messages import InboundContext, process_inbound_message

TENANT_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())
MESSAGE_ID = str(uuid.uuid4())


def _ctx() -> dict:
    session = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return {"session_factory": factory, "http": AsyncMock(), "job_try": 1}


def _inbound(
    state: str = "agent",
    credit_balance: int = 1000,
    human_last_seen_at=None,
) -> InboundContext:
    return InboundContext(
        conversation_state=state,
        contact_phone_number="5511888888888",
        message_content="Olá",
        phone_number_id="PNID",
        access_token_encrypted="token-cifrado",
        credit_balance=credit_balance,
        end_customer_billing_enabled=False,
        end_customer_tokens_per_credit=None,
        end_customer_balance=0,
        end_customer_packages=[],
        human_last_seen_at=human_last_seen_at,
    )


FIRST_MESSAGE_ID = uuid.uuid4()


@pytest.fixture
def patched(monkeypatch):
    mocks = {
        "load": AsyncMock(return_value=_inbound()),
        "decrypt": MagicMock(return_value="token-claro"),
        "send": AsyncMock(
            return_value={"responses": ["resposta 1", "resposta 2"], "tokens_used": 3500}
        ),
        "persist": AsyncMock(return_value=FIRST_MESSAGE_ID),
        "debitar": AsyncMock(),
        "sync": AsyncMock(),
    }
    monkeypatch.setattr(messages_task, "_load_context", mocks["load"])
    monkeypatch.setattr(messages_task, "decrypt_access_token", mocks["decrypt"])
    monkeypatch.setattr(messages_task, "send_message_to_agents", mocks["send"])
    monkeypatch.setattr(messages_task, "_persist_agent_responses", mocks["persist"])
    monkeypatch.setattr(messages_task, "_debitar_creditos", mocks["debitar"])
    monkeypatch.setattr(messages_task, "sync_context_to_agents", mocks["sync"])
    return mocks


async def test_agent_flow_persists_responses(patched) -> None:
    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["decrypt"].assert_called_once_with("token-cifrado")
    patched["send"].assert_awaited_once()
    assert patched["send"].await_args.kwargs["access_token"] == "token-claro"
    assert patched["send"].await_args.kwargs["message"] == "Olá"
    patched["persist"].assert_awaited_once()
    assert patched["persist"].await_args.args[3] == ["resposta 1", "resposta 2"]


async def test_consumo_convertido_em_creditos_com_ceil(patched) -> None:
    # 3500 tokens / 1000 tokens por crédito = 3.5 → ceil → 4 créditos
    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    persist_args = patched["persist"].await_args.args
    assert persist_args[4] == 3500  # tokens_used
    assert persist_args[5] == 4  # credits
    patched["debitar"].assert_awaited_once_with(
        patched["debitar"].await_args.args[0], TENANT_ID, FIRST_MESSAGE_ID, 3500, 4, 0, 0
    )


async def test_sem_tokens_nao_debita(patched) -> None:
    patched["send"].return_value = {"responses": ["resposta"], "tokens_used": 0}

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["persist"].assert_awaited_once()
    patched["debitar"].assert_not_awaited()


async def test_human_state_skips_agent(patched) -> None:
    patched["load"].return_value = _inbound(state="human", human_last_seen_at=datetime.now(UTC))

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_not_awaited()
    patched["persist"].assert_not_awaited()


async def test_human_nao_expirado_sincroniza_contexto(patched) -> None:
    patched["load"].return_value = _inbound(state="human", human_last_seen_at=datetime.now(UTC))

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["sync"].assert_awaited_once()
    kwargs = patched["sync"].await_args.kwargs
    assert kwargs["role"] == "contact"
    assert kwargs["content"] == "Olá"
    patched["send"].assert_not_awaited()


async def test_human_expirado_reativa_ia_e_chama_agente(patched) -> None:
    patched["load"].return_value = _inbound(
        state="human", human_last_seen_at=datetime.now(UTC) - timedelta(seconds=999)
    )
    ctx = _ctx()

    await process_inbound_message(ctx, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    session = ctx["session_factory"].return_value.__aenter__.return_value
    session.execute.assert_awaited()  # UPDATE state='agent'
    patched["send"].assert_awaited_once()
    patched["persist"].assert_awaited_once()


async def test_human_sem_last_seen_e_tratado_como_expirado(patched) -> None:
    patched["load"].return_value = _inbound(state="human", human_last_seen_at=None)

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()


async def test_saldo_esgotado_sincroniza_contexto(patched) -> None:
    patched["load"].return_value = _inbound(credit_balance=0)

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["sync"].assert_awaited_once()
    patched["send"].assert_not_awaited()


async def test_falha_no_sync_nao_quebra(patched) -> None:
    patched["sync"].side_effect = httpx.ConnectError("agents fora do ar")
    patched["load"].return_value = _inbound(state="human", human_last_seen_at=datetime.now(UTC))

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)
    # não levanta — best-effort


async def test_saldo_esgotado_nao_chama_agente(patched) -> None:
    patched["load"].return_value = _inbound(credit_balance=0)

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_not_awaited()
    patched["persist"].assert_not_awaited()
    patched["debitar"].assert_not_awaited()


async def test_saldo_negativo_nao_chama_agente(patched) -> None:
    patched["load"].return_value = _inbound(credit_balance=-50)

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_not_awaited()


async def test_saldo_positivo_chama_agente_normalmente(patched) -> None:
    patched["load"].return_value = _inbound(credit_balance=1)

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()


async def test_missing_context_returns_early(patched) -> None:
    patched["load"].return_value = None

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_not_awaited()


async def test_debounced_202_does_not_persist(patched) -> None:
    patched["send"].return_value = None

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["persist"].assert_not_awaited()


async def test_http_error_raises_retry(patched) -> None:
    patched["send"].side_effect = httpx.ConnectError("agents fora do ar")

    with pytest.raises(Retry):
        await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["persist"].assert_not_awaited()


async def test_delivery_failures_repassado_ao_persistir(patched) -> None:
    patched["send"].return_value = {
        "responses": ["resposta 1", "resposta 2"],
        "tokens_used": 100,
        "delivery_failures": [1],
    }

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    persist_args = patched["persist"].await_args.args
    assert persist_args[6] == {1}


def test_decrypt_access_token_roundtrip(monkeypatch) -> None:
    key = Fernet.generate_key()
    monkeypatch.setattr(settings, "whatsapp_token_encryption_key", key.decode())
    encrypted = Fernet(key).encrypt(b"meu-token").decode()

    assert decrypt_access_token(encrypted) == "meu-token"


def test_decrypt_without_key_raises(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_token_encryption_key", "")

    with pytest.raises(RuntimeError):
        decrypt_access_token("qualquer")


async def test_esgotadas_tentativas_vira_conversa_pra_human(patched) -> None:
    patched["send"].side_effect = httpx.ConnectError("agents fora do ar")
    ctx = _ctx()
    ctx["job_try"] = 5

    await process_inbound_message(ctx, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    session = ctx["session_factory"].return_value.__aenter__.return_value
    session.execute.assert_awaited()
    session.commit.assert_awaited()
    patched["persist"].assert_not_awaited()


async def test_load_context_seta_app_tenant_id(patched) -> None:
    ctx = _ctx()
    session = ctx["session_factory"].return_value.__aenter__.return_value

    await process_inbound_message(ctx, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    set_config_calls = [
        call
        for call in session.execute.await_args_list
        if len(call.args) > 1 and call.args[1] == {"tenant_id": TENANT_ID}
    ]
    assert len(set_config_calls) >= 1


def _inbound_com_billing(balance: int, tokens_per_credit: int = 500) -> InboundContext:
    return InboundContext(
        conversation_state="agent",
        contact_phone_number="5511888888888",
        message_content="Olá",
        phone_number_id="PNID",
        access_token_encrypted="token-cifrado",
        credit_balance=1000,
        end_customer_billing_enabled=True,
        end_customer_tokens_per_credit=tokens_per_credit,
        end_customer_balance=balance,
        end_customer_packages=[
            {"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}
        ],
    )


async def test_billing_habilitado_com_saldo_debita_cliente_final(monkeypatch) -> None:
    mocks = {
        "load": AsyncMock(return_value=_inbound_com_billing(balance=1000)),
        "decrypt": MagicMock(return_value="token-claro"),
        "send": AsyncMock(return_value={"responses": ["oi"], "tokens_used": 2000}),
        "persist": AsyncMock(return_value=FIRST_MESSAGE_ID),
        "debitar": AsyncMock(),
        "debitar_cliente_final": AsyncMock(),
    }
    monkeypatch.setattr(messages_task, "_load_context", mocks["load"])
    monkeypatch.setattr(messages_task, "decrypt_access_token", mocks["decrypt"])
    monkeypatch.setattr(messages_task, "send_message_to_agents", mocks["send"])
    monkeypatch.setattr(messages_task, "_persist_agent_responses", mocks["persist"])
    monkeypatch.setattr(messages_task, "_debitar_creditos", mocks["debitar"])
    monkeypatch.setattr(
        messages_task, "_debitar_creditos_cliente_final", mocks["debitar_cliente_final"]
    )

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    mocks["send"].assert_awaited_once()
    assert mocks["send"].await_args.kwargs["end_customer_billing"]["balance"] == 1000
    mocks["debitar_cliente_final"].assert_awaited_once()
    assert mocks["debitar_cliente_final"].await_args.args[4] == 2000  # tokens_used
    assert mocks["debitar_cliente_final"].await_args.args[5] == 4  # ceil(2000/500)


async def test_billing_habilitado_sem_saldo_nao_debita_cliente_final(monkeypatch) -> None:
    mocks = {
        "load": AsyncMock(return_value=_inbound_com_billing(balance=0)),
        "decrypt": MagicMock(return_value="token-claro"),
        "send": AsyncMock(return_value={"responses": ["oi"], "tokens_used": 2000}),
        "persist": AsyncMock(return_value=FIRST_MESSAGE_ID),
        "debitar": AsyncMock(),
        "debitar_cliente_final": AsyncMock(),
    }
    monkeypatch.setattr(messages_task, "_load_context", mocks["load"])
    monkeypatch.setattr(messages_task, "decrypt_access_token", mocks["decrypt"])
    monkeypatch.setattr(messages_task, "send_message_to_agents", mocks["send"])
    monkeypatch.setattr(messages_task, "_persist_agent_responses", mocks["persist"])
    monkeypatch.setattr(messages_task, "_debitar_creditos", mocks["debitar"])
    monkeypatch.setattr(
        messages_task, "_debitar_creditos_cliente_final", mocks["debitar_cliente_final"]
    )

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    mocks["send"].assert_awaited_once()
    assert mocks["send"].await_args.kwargs["end_customer_billing"]["balance"] == 0
    mocks["debitar_cliente_final"].assert_not_awaited()


async def test_billing_desabilitado_nao_manda_bloco_e_nao_debita(monkeypatch) -> None:
    mocks = {
        "load": AsyncMock(return_value=_inbound()),  # helper já existente, sem billing habilitado
        "decrypt": MagicMock(return_value="token-claro"),
        "send": AsyncMock(return_value={"responses": ["oi"], "tokens_used": 2000}),
        "persist": AsyncMock(return_value=FIRST_MESSAGE_ID),
        "debitar": AsyncMock(),
        "debitar_cliente_final": AsyncMock(),
    }
    monkeypatch.setattr(messages_task, "_load_context", mocks["load"])
    monkeypatch.setattr(messages_task, "decrypt_access_token", mocks["decrypt"])
    monkeypatch.setattr(messages_task, "send_message_to_agents", mocks["send"])
    monkeypatch.setattr(messages_task, "_persist_agent_responses", mocks["persist"])
    monkeypatch.setattr(messages_task, "_debitar_creditos", mocks["debitar"])
    monkeypatch.setattr(
        messages_task, "_debitar_creditos_cliente_final", mocks["debitar_cliente_final"]
    )

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert "end_customer_billing" not in mocks["send"].await_args.kwargs
    mocks["debitar_cliente_final"].assert_not_awaited()
