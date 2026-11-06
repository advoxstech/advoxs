"""Z-API Omni (WhatsApp oficial) no worker: envio pela camada de compatibilidade,
billing gate com botões ou lista numerada, caixa de saída, follow-up com modelo
e download de anexos."""

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.clients.media as media_client
import app.clients.omni as omni_client
import app.clients.zapi as zapi_client
from app.billing_gate import handle_billing_gate
from app.clients.omni import OmniTemplateError
from app.clients.zapi import send_zapi_button_list, send_zapi_text_message
from app.config import settings
from app.tasks import follow_ups, outbound_outbox
from app.tasks.messages import InboundContext

TENANT_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def omni_settings(monkeypatch):
    monkeypatch.setattr(settings, "omni_secret_key", "segredo-omni")
    monkeypatch.setattr(settings, "omni_compat_base_url", "https://zapi.omni.z-api.io")
    monkeypatch.setattr(settings, "omni_api_base_url", "https://api.omni.z-api.io")


def _http(monkeypatch, module, response) -> AsyncMock:
    client = AsyncMock()
    client.post.return_value = response
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(module.httpx, "AsyncClient", MagicMock(return_value=cm))
    return client


def _response(status_code: int, body: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.is_error = status_code >= 400
    response.json.return_value = body
    return response


# --- camada de compatibilidade Z-API ---------------------------------------------


async def test_texto_omni_usa_canal_e_secret_key(monkeypatch) -> None:
    client = _http(monkeypatch, zapi_client, _response(200, {"messageId": "m1"}))

    await send_zapi_text_message("canal-1", "tok-1", None, "5511999998888", "Olá", omni=True)

    url = client.post.call_args.args[0]
    headers = client.post.call_args.kwargs["headers"]
    assert url == "https://zapi.omni.z-api.io/channels/canal-1/token/tok-1/send-text"
    assert headers["Authorization"] == "Bearer segredo-omni"
    assert "Client-Token" not in headers


async def test_texto_zapi_classica_segue_igual(monkeypatch) -> None:
    client = _http(monkeypatch, zapi_client, _response(200, {"messageId": "m1"}))

    await send_zapi_text_message("inst-1", "tok-1", "client-tok", "5511999998888", "Olá")

    assert client.post.call_args.args[0].startswith("https://api.z-api.io/instances/inst-1/")
    assert client.post.call_args.kwargs["headers"]["Client-Token"] == "client-tok"


async def test_botoes_limitados_a_tres(monkeypatch) -> None:
    client = _http(monkeypatch, zapi_client, _response(200, {"messageId": "m1"}))
    botoes = [{"id": str(i), "label": f"P{i}"} for i in range(5)]

    await send_zapi_button_list("canal-1", "tok-1", None, "55", "Escolha", botoes, omni=True)

    payload = client.post.call_args.kwargs["json"]
    assert client.post.call_args.args[0].endswith("/send-button-list")
    assert len(payload["buttonList"]["buttons"]) == 3


# --- billing gate ----------------------------------------------------------------


def _inbound(packages, **overrides) -> InboundContext:
    base = InboundContext(
        conversation_state="billing_gate",
        contact_phone_number="5511999998888",
        message_content="oi",
        whatsapp_provider="omni",
        phone_number_id=None,
        access_token_encrypted=None,
        zapi_instance_id="canal-1",
        zapi_instance_token_encrypted="tok-cifrado",
        zapi_client_token_encrypted=None,
        credit_balance=Decimal(1000),
        end_customer_billing_enabled=True,
        end_customer_balance=Decimal(0),
        end_customer_packages=packages,
        agents=[],
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _session():
    session = AsyncMock()
    session.execute.return_value = MagicMock()
    session.execute.return_value.one_or_none.return_value = SimpleNamespace(
        state="billing_gate", human_only=False
    )
    session.scalar = AsyncMock(return_value=None)
    return session


def _pacote(i: int, nome: str | None = None) -> dict:
    return {
        "id": f"pkg-{i}",
        "name": nome or f"Pacote {i}",
        "price_brl": "50.00",
        "credits_granted": 500,
    }


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setattr("app.billing_gate.decrypt_access_token", lambda v: "tok-claro")
    mocks = {
        "texto": AsyncMock(),
        "botoes": AsyncMock(),
        "lista": AsyncMock(),
        "meta": AsyncMock(),
    }
    monkeypatch.setattr("app.billing_gate.send_zapi_text_message", mocks["texto"])
    monkeypatch.setattr("app.billing_gate.send_zapi_button_list", mocks["botoes"])
    monkeypatch.setattr("app.billing_gate.send_zapi_option_list", mocks["lista"])
    monkeypatch.setattr("app.billing_gate.send_text_message", mocks["meta"])
    return mocks


async def test_ate_tres_pacotes_viram_botoes(gate) -> None:
    inbound = _inbound([_pacote(1), _pacote(2)], billing_gate_step=None)

    await handle_billing_gate(_session(), TENANT_ID, CONVERSATION_ID, inbound)

    gate["botoes"].assert_awaited_once()
    kwargs = gate["botoes"].await_args.kwargs
    assert kwargs["omni"] is True
    assert [b["label"] for b in kwargs["buttons"]] == ["Pacote 1", "Pacote 2"]
    assert gate["texto"].await_args.kwargs["omni"] is True
    gate["lista"].assert_not_awaited()
    gate["meta"].assert_not_awaited()


async def test_mais_de_tres_pacotes_viram_lista_numerada(gate) -> None:
    inbound = _inbound([_pacote(i) for i in range(1, 5)], billing_gate_step=None)

    await handle_billing_gate(_session(), TENANT_ID, CONVERSATION_ID, inbound)

    gate["botoes"].assert_not_awaited()
    numerada = gate["texto"].await_args_list[-1].kwargs["text"]
    assert "Responda com o número" in numerada
    assert "4. Pacote 4" in numerada


async def test_nome_longo_demais_para_botao_vira_lista_numerada(gate) -> None:
    inbound = _inbound([_pacote(1, "Pacote com nome bem comprido")], billing_gate_step=None)

    await handle_billing_gate(_session(), TENANT_ID, CONVERSATION_ID, inbound)

    gate["botoes"].assert_not_awaited()
    assert "1. Pacote com nome bem comprido" in gate["texto"].await_args_list[-1].kwargs["text"]


async def test_cliente_responde_com_o_numero_do_pacote(gate, monkeypatch) -> None:
    checkout = AsyncMock(return_value="https://checkout.stripe.com/x")
    monkeypatch.setattr("app.billing_gate.create_end_customer_checkout", checkout)
    pacotes = [_pacote(i) for i in range(1, 5)]
    inbound = _inbound(
        pacotes, billing_gate_step="aguardando_selecao_pacote", message_content=" 3. "
    )

    await handle_billing_gate(_session(), TENANT_ID, CONVERSATION_ID, inbound)

    assert checkout.await_args.kwargs["package_id"] == "pkg-3"


# --- caixa de saída (resposta do atendente / documento) ---------------------------


def _delivery(**overrides):
    base = dict(
        provider="omni",
        content="Olá",
        media_url=None,
        contact_phone_number="5511999998888",
        omni_channel_id="canal-1",
        omni_channel_token_encrypted="tok-cifrado",
        zapi_instance_id=None,
        zapi_instance_token_encrypted=None,
        zapi_client_token_encrypted=None,
        phone_number_id=None,
        access_token_encrypted=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_entrega_de_texto_pelo_omni(monkeypatch) -> None:
    monkeypatch.setattr(outbound_outbox, "decrypt_access_token", lambda v: "tok-claro")
    texto = AsyncMock()
    monkeypatch.setattr(outbound_outbox, "send_zapi_text_message", texto)

    await outbound_outbox._send_delivery(_delivery())

    assert texto.await_args.args[:2] == ("canal-1", "tok-claro")
    assert texto.await_args.kwargs == {"omni": True}


async def test_entrega_de_documento_pelo_omni(monkeypatch) -> None:
    monkeypatch.setattr(outbound_outbox, "decrypt_access_token", lambda v: "tok-claro")
    documento = AsyncMock()
    monkeypatch.setattr(outbound_outbox, "send_zapi_document_message", documento)

    await outbound_outbox._send_delivery(
        _delivery(media_url="https://agents.exemplo/doc.pdf", content="📄 Contrato.pdf")
    )

    assert documento.await_args.args[4:] == ("https://agents.exemplo/doc.pdf", "Contrato.pdf")
    assert documento.await_args.kwargs == {"omni": True}


# --- follow-up com modelo -------------------------------------------------------


_TEMPLATE = {
    "name": "lembrete",
    "language": "pt_BR",
    "parameter_format": "POSITIONAL",
    "header_text": None,
    "body_text": "Olá {{1}}",
    "footer_text": None,
    "param_names": ["1"],
}


async def test_follow_up_envia_modelo_pelo_omni(monkeypatch) -> None:
    enviar = AsyncMock(return_value="msg-1")
    monkeypatch.setattr(follow_ups, "send_omni_template", enviar)
    batch = SimpleNamespace(
        provider="omni", template=_TEMPLATE, params=[{"source": "contact_name", "value": "cliente"}]
    )
    number = SimpleNamespace(provider="omni", omni_channel_id="canal-1")

    content = await follow_ups._send(batch, number, "5511999998888", "Ana")

    assert enviar.await_args.kwargs["channel_id"] == "canal-1"
    assert enviar.await_args.kwargs["name"] == "lembrete"
    assert content == "Olá Ana"


async def test_follow_up_omni_recusado_vira_falha_definitiva(monkeypatch) -> None:
    monkeypatch.setattr(
        follow_ups,
        "send_omni_template",
        AsyncMock(side_effect=OmniTemplateError("Modelo pausado", retryable=False)),
    )
    batch = SimpleNamespace(provider="omni", template=_TEMPLATE, params=[])
    number = SimpleNamespace(provider="omni", omni_channel_id="canal-1")

    with pytest.raises(follow_ups.FollowUpSendError) as erro:
        await follow_ups._send(batch, number, "5511999998888", "")

    assert erro.value.retryable is False


async def test_envio_de_modelo_omni_classifica_erros(monkeypatch) -> None:
    _http(monkeypatch, omni_client, _response(503, {"message": "indisponível"}))
    with pytest.raises(OmniTemplateError) as temporario:
        await omni_client.send_template("canal-1", "55", "lembrete", "pt_BR", [])
    assert temporario.value.retryable is True

    _http(monkeypatch, omni_client, _response(400, {"message": "Modelo não aprovado"}))
    with pytest.raises(OmniTemplateError) as definitivo:
        await omni_client.send_template("canal-1", "55", "lembrete", "pt_BR", [])
    assert definitivo.value.retryable is False
    assert "Modelo não aprovado" in str(definitivo.value)


# --- download de anexo ----------------------------------------------------------


def _stream_client(monkeypatch) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()

    async def chunks():
        yield b"%PDF"

    response.aiter_bytes = chunks
    response.headers = {}
    stream_cm = MagicMock()
    stream_cm.__aenter__ = AsyncMock(return_value=response)
    stream_cm.__aexit__ = AsyncMock(return_value=False)
    client = MagicMock()
    client.stream = MagicMock(return_value=stream_cm)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(media_client.httpx, "AsyncClient", MagicMock(return_value=cm))
    return client


@pytest.mark.parametrize(
    ("url", "com_chave"),
    [
        ("https://files.omni.z-api.io/doc.pdf", True),
        ("https://storage.googleapis.com/bucket/doc.pdf", False),
    ],
)
async def test_secret_key_so_vai_para_o_dominio_do_omni(monkeypatch, url, com_chave) -> None:
    client = _stream_client(monkeypatch)

    await media_client.download_zapi_media(url, None, 1024, omni=True)

    headers = client.stream.call_args.kwargs["headers"]
    assert ("Authorization" in headers) is com_chave
