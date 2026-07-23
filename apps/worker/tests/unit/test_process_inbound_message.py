import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from arq.worker import Retry
from cryptography.fernet import Fernet

from app.clients.whatsapp import WhatsAppSendError
from app.config import settings
from app.crypto import decrypt_access_token
from app.tasks import messages as messages_task
from app.tasks.messages import InboundContext, process_inbound_message

TENANT_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())
MESSAGE_ID = str(uuid.uuid4())

PRICING_CONFIG = SimpleNamespace(
    id=uuid.uuid4(),
    tokens_per_credit=1000,
    input_weight=Decimal("0.3"),
    output_weight=Decimal("1.0"),
)


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
        credit_balance=Decimal(credit_balance),
        end_customer_billing_enabled=False,
        end_customer_balance=Decimal(0),
        end_customer_packages=[],
        agents=[],
        human_last_seen_at=human_last_seen_at,
    )


FIRST_MESSAGE_ID = uuid.uuid4()


@pytest.fixture
def patched(monkeypatch):
    mocks = {
        "load": AsyncMock(return_value=_inbound()),
        "decrypt": MagicMock(return_value="token-claro"),
        "send": AsyncMock(
            return_value={
                "responses": ["resposta 1", "resposta 2"],
                "tokens_used": 3500,
                "tokens_input": 2500,
                "tokens_output": 1000,
            }
        ),
        "persist": AsyncMock(return_value=FIRST_MESSAGE_ID),
        "debitar": AsyncMock(),
        "debitar_cliente_final": AsyncMock(),
        "pricing": AsyncMock(return_value=PRICING_CONFIG),
        "sync": AsyncMock(),
    }
    monkeypatch.setattr(messages_task, "_load_context", mocks["load"])
    monkeypatch.setattr(messages_task, "decrypt_access_token", mocks["decrypt"])
    monkeypatch.setattr(messages_task, "send_message_to_agents", mocks["send"])
    monkeypatch.setattr(messages_task, "_persist_agent_responses", mocks["persist"])
    monkeypatch.setattr(messages_task, "_debitar_creditos", mocks["debitar"])
    monkeypatch.setattr(
        messages_task, "_debitar_creditos_cliente_final", mocks["debitar_cliente_final"]
    )
    monkeypatch.setattr(messages_task, "get_current_pricing_config", mocks["pricing"])
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


async def test_consumo_ponderado_arredonda_pro_inteiro(patched) -> None:
    # 2500*0.3 + 1000*1.0 = 1750 tokens ponderados / 1000 = 1.75 créditos ->
    # HALF_UP sobe pra 2
    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    persist_args = patched["persist"].await_args.args
    assert persist_args[4] == 3500  # tokens_used
    assert persist_args[5] == Decimal("2")  # credits arredondados
    patched["debitar"].assert_awaited_once_with(
        patched["debitar"].await_args.args[0],
        TENANT_ID,
        FIRST_MESSAGE_ID,
        3500,
        Decimal("2"),
        2500,
        1000,
        PRICING_CONFIG.id,
    )
    # Sem cobrança do cliente final: só o estoque do tenant é debitado.
    patched["debitar_cliente_final"].assert_not_awaited()


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


async def test_erro_nao_http_tambem_reagenda(patched) -> None:
    """Regressão: um TypeError (ex: bug de serialização, já aconteceu em
    produção) precisa cair no mesmo tratamento de httpx.HTTPError — não pode
    subir incapturado e fazer o Arq esgotar as tentativas em silêncio."""
    patched["send"].side_effect = TypeError("Object of type Decimal is not JSON serializable")

    with pytest.raises(Retry):
        await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["persist"].assert_not_awaited()


async def test_erro_nao_http_esgotadas_tentativas_vira_conversa_pra_human(patched) -> None:
    patched["send"].side_effect = TypeError("Object of type Decimal is not JSON serializable")
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


def _inbound_com_billing(
    balance: int, credit_balance: int = 1000, exempt: bool = False
) -> InboundContext:
    return InboundContext(
        conversation_state="agent",
        contact_phone_number="5511888888888",
        message_content="Olá",
        phone_number_id="PNID",
        access_token_encrypted="token-cifrado",
        credit_balance=Decimal(credit_balance),
        end_customer_billing_enabled=True,
        end_customer_balance=Decimal(balance),
        end_customer_packages=[
            {"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}
        ],
        agents=[],
        end_customer_billing_exempt=exempt,
    )


async def test_moeda_unica_debita_so_o_cliente_final(patched) -> None:
    patched["load"].return_value = _inbound_com_billing(balance=1000)
    patched["send"].return_value = {"responses": ["oi"], "tokens_used": 2000}

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()
    assert patched["send"].await_args.kwargs["end_customer_billing"]["balance"] == 1000
    # Moeda única: o turno custeado pelo cliente NÃO debita o tenant de novo.
    patched["debitar"].assert_not_awaited()
    patched["debitar_cliente_final"].assert_awaited_once()
    args = patched["debitar_cliente_final"].await_args.args
    assert args[4] == 2000  # tokens_used
    # Sem breakdown na resposta -> fallback: tudo como output -> 2000/1000 = 2
    assert args[5] == Decimal("2")
    assert args[8] == PRICING_CONFIG.id


async def test_billing_habilitado_sem_saldo_debita_o_tenant(patched) -> None:
    # Cliente sem saldo: a secretária oferece pacotes — turno custeado pelo tenant.
    patched["load"].return_value = _inbound_com_billing(balance=0)
    patched["send"].return_value = {"responses": ["oi"], "tokens_used": 2000}

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()
    assert patched["send"].await_args.kwargs["end_customer_billing"]["balance"] == 0
    patched["debitar_cliente_final"].assert_not_awaited()
    patched["debitar"].assert_awaited_once()


async def test_tenant_zerado_mas_cliente_final_com_saldo_roda_o_agente(patched) -> None:
    # O crédito do cliente já saiu do estoque do tenant na revenda — o turno
    # custeado pelo cliente roda mesmo com o estoque do tenant esgotado.
    patched["load"].return_value = _inbound_com_billing(balance=500, credit_balance=0)
    patched["send"].return_value = {"responses": ["oi"], "tokens_used": 2000}

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()
    patched["debitar_cliente_final"].assert_awaited_once()
    patched["debitar"].assert_not_awaited()


async def test_tenant_zerado_e_cliente_sem_saldo_continua_em_silencio(patched) -> None:
    patched["load"].return_value = _inbound_com_billing(balance=0, credit_balance=0)

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_not_awaited()
    patched["sync"].assert_awaited_once()


async def test_billing_desabilitado_nao_manda_bloco_e_nao_debita(patched) -> None:
    patched["send"].return_value = {"responses": ["oi"], "tokens_used": 2000}

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert "end_customer_billing" not in patched["send"].await_args.kwargs
    patched["debitar_cliente_final"].assert_not_awaited()


async def test_agents_do_inbound_e_repassado_ao_send_message(patched) -> None:
    agents_payload = [
        {
            "id": "a1",
            "name": "Secretária",
            "instructions": "x",
            "is_entry_point": True,
            "knowledge_base_file_ids": [],
        }
    ]
    patched["load"].return_value = InboundContext(
        conversation_state="agent",
        contact_phone_number="5511888888888",
        message_content="Olá",
        phone_number_id="PNID",
        access_token_encrypted="token-cifrado",
        credit_balance=Decimal(1000),
        end_customer_billing_enabled=False,
        end_customer_balance=Decimal(0),
        end_customer_packages=[],
        agents=agents_payload,
    )

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert patched["send"].await_args.kwargs["agents"] == agents_payload


async def test_entra_no_billing_gate_e_nao_chama_agents(monkeypatch) -> None:
    entrada_mock = AsyncMock(return_value=True)
    handle_mock = AsyncMock()
    monkeypatch.setattr(messages_task, "maybe_enter_gate", entrada_mock)
    monkeypatch.setattr(messages_task, "handle_billing_gate", handle_mock)
    ctx = _ctx()
    session = AsyncMock()
    ctx["session_factory"].return_value.__aenter__ = AsyncMock(return_value=session)

    monkeypatch.setattr(
        messages_task,
        "_load_context",
        AsyncMock(return_value=_inbound(state="agent", credit_balance=1000)),
    )

    await process_inbound_message(ctx, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    entrada_mock.assert_awaited_once()
    handle_mock.assert_awaited_once()
    ctx["http"].post.assert_not_called()


async def test_falha_dentro_do_billing_gate_escala_pra_human_sem_propagar(monkeypatch) -> None:
    """Regressão: uma falha de envio (ex: WhatsAppSendError ao mandar a lista
    de pacotes) dentro de handle_billing_gate não pode propagar incapturada —
    isso mataria o job do arq e deixaria a conversa travada em
    state=billing_gate pra sempre (a válvula de MAX_RETRIES só dispara em
    RESPONSE não reconhecida, nunca numa falha de envio)."""
    entrada_mock = AsyncMock(return_value=True)
    handle_mock = AsyncMock(side_effect=WhatsAppSendError("Graph API HTTP 500: erro simulado"))
    monkeypatch.setattr(messages_task, "maybe_enter_gate", entrada_mock)
    monkeypatch.setattr(messages_task, "handle_billing_gate", handle_mock)
    ctx = _ctx()
    session = AsyncMock()
    ctx["session_factory"].return_value.__aenter__ = AsyncMock(return_value=session)

    monkeypatch.setattr(
        messages_task,
        "_load_context",
        AsyncMock(return_value=_inbound(state="agent", credit_balance=1000)),
    )

    # Não deve propagar — se propagasse, este await levantaria WhatsAppSendError.
    await process_inbound_message(ctx, TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    handle_mock.assert_awaited_once()
    update_values = session.execute.await_args.args[0]
    compiled = str(update_values.compile(compile_kwargs={"literal_binds": True}))
    assert "state='human'" in compiled


async def test_contato_isento_nunca_e_customer_funded(patched) -> None:
    patched["load"].return_value = _inbound_com_billing(balance=1000, exempt=True)
    patched["send"].return_value = {"responses": ["oi"], "tokens_used": 2000}

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_awaited_once()
    assert "end_customer_billing" not in patched["send"].await_args.kwargs


async def test_contato_isento_com_saldo_do_tenant_zerado_fica_em_silencio(patched) -> None:
    patched["load"].return_value = _inbound_com_billing(
        balance=1000, credit_balance=0, exempt=True
    )

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_not_awaited()
