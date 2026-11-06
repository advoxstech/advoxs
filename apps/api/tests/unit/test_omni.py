"""Z-API Omni (WhatsApp oficial) no api: assinatura do webhook, recebimento,
cadastro pelo admin, envio avulso, modelos de follow-up e configuração."""

import gzip
import hashlib
import hmac
import json
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.follow_ups as follow_ups_module
import app.services.omni_connection as omni_connection_module
import app.services.whatsapp_inbound as inbound_module
import app.services.whatsapp_outbound as outbound_module
from app.api.deps import PlatformAdminContext, get_current_platform_admin
from app.clients.omni import OmniApiError, valid_webhook_signature
from app.core.db import get_system_session
from app.core.queue import get_arq_pool
from app.main import app
from config_validation import validate_config

# --- assinatura HMAC: exemplo oficial da documentação do Omni ---------------------

DOC_SECRET = "a7f3c81e9d2b4605f1a8c3e7b9d40f6218273645a8b9c0d1e2f3041526374859"
DOC_BODY = (
    b'[{"field":"message_template_status_update","identifier":"01A0A4FBC8CD7DCBBBFAA8382F5514A5",'
    b'"type":"TEMPLATE_STATUS","value":{"event":"APPROVED","message_template_category":"UTILITY",'
    b'"message_template_id":"1587155656151346","message_template_language":"pt_BR",'
    b'"message_template_name":"exemplo_teste_assinatura_webhook","reason":"NONE"},'
    b'"waba_id":"845974408299669"}]'
)
DOC_SIGNATURE = "t=1789474359,v1=c9a6812419c87dc26841fd802f1b4704f7dfe868b178ba55f9c432642be67230"
DOC_IDEMPOTENCY = "webhook.delivery.event:0:539402"
DOC_NOW = 1789474359


def test_assinatura_do_exemplo_oficial_confere() -> None:
    assert valid_webhook_signature(
        DOC_SECRET, DOC_BODY, DOC_SIGNATURE, DOC_IDEMPOTENCY, now=DOC_NOW
    )


def test_assinatura_vale_para_corpo_em_gzip() -> None:
    assert valid_webhook_signature(
        DOC_SECRET, gzip.compress(DOC_BODY), DOC_SIGNATURE, DOC_IDEMPOTENCY, "gzip", now=DOC_NOW
    )


@pytest.mark.parametrize(
    ("body", "signature", "idempotency", "now"),
    [
        (DOC_BODY + b" ", DOC_SIGNATURE, DOC_IDEMPOTENCY, DOC_NOW),  # corpo alterado
        (DOC_BODY, DOC_SIGNATURE, "webhook.delivery.event:0:539403", DOC_NOW),  # offset
        (DOC_BODY, DOC_SIGNATURE, DOC_IDEMPOTENCY, DOC_NOW + 301),  # velha demais
        (DOC_BODY, None, DOC_IDEMPOTENCY, DOC_NOW),  # sem assinatura
        (DOC_BODY, "lixo", DOC_IDEMPOTENCY, DOC_NOW),  # formato inválido
    ],
)
def test_assinatura_recusada(body, signature, idempotency, now) -> None:
    assert not valid_webhook_signature(DOC_SECRET, body, signature, idempotency, now=now)


# --- webhook ---------------------------------------------------------------------

PATH_SECRET = "segredo-da-url"
WEBHOOK_PATH = f"/api/v1/webhooks/omni/{PATH_SECRET}"
SIGNING_SECRET = "segredo-hmac"
IDEMPOTENCY = "webhook.delivery.event:3:42"

TEXT_PAYLOAD = {
    "phone": "5511888888888",
    "messageId": "wamid-1",
    "fromMe": False,
    "text": {"message": "Olá"},
}


def _signed(payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    t = str(int(time.time()))
    topic, partition, offset = IDEMPOTENCY.rsplit(":", 2)
    canonical = b"\n".join([t.encode(), topic.encode(), partition.encode(), offset.encode(), body])
    v1 = hmac.new(SIGNING_SECRET.encode(), canonical, hashlib.sha256).hexdigest()
    return body, {
        "x-webhook-signature": f"t={t},v1={v1}",
        "x-idempotency-key": IDEMPOTENCY,
        "content-type": "application/json",
    }


@pytest.fixture
def arq_pool():
    return AsyncMock()


@pytest.fixture
def fake_session():
    session = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def webhook_client(fake_session, arq_pool, monkeypatch):
    monkeypatch.setattr(inbound_module, "decrypt_access_token", lambda v: v)

    async def override_session():
        yield fake_session

    async def override_arq():
        return arq_pool

    app.dependency_overrides[get_system_session] = override_session
    app.dependency_overrides[get_arq_pool] = override_arq
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _omni_number(**overrides):
    base = dict(
        tenant_id=uuid.uuid4(),
        provider="omni",
        omni_channel_id="canal-1",
        omni_webhook_signing_secret_encrypted=SIGNING_SECRET,
        status="connected",
    )
    base.update(overrides)
    return MagicMock(**base)


def test_mensagem_assinada_e_gravada_e_enfileirada(webhook_client, fake_session, arq_pool) -> None:
    number = _omni_number()
    conversation = MagicMock(id=uuid.uuid4(), tenant_id=number.tenant_id)
    fake_session.scalar.side_effect = [number, None, conversation]
    body, headers = _signed(TEXT_PAYLOAD)

    response = webhook_client.post(WEBHOOK_PATH, content=body, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"received": 1}
    arq_pool.enqueue_job.assert_awaited_once()


def test_assinatura_invalida_e_recusada(webhook_client, fake_session, arq_pool) -> None:
    fake_session.scalar.side_effect = [_omni_number(omni_webhook_signing_secret_encrypted="outro")]
    body, headers = _signed(TEXT_PAYLOAD)

    response = webhook_client.post(WEBHOOK_PATH, content=body, headers=headers)

    assert response.status_code == 401
    arq_pool.enqueue_job.assert_not_awaited()


def test_segredo_da_url_desconhecido_e_recusado(webhook_client, fake_session, arq_pool) -> None:
    fake_session.scalar.side_effect = [None]
    body, headers = _signed(TEXT_PAYLOAD)

    response = webhook_client.post(WEBHOOK_PATH, content=body, headers=headers)

    assert response.status_code == 401
    arq_pool.enqueue_job.assert_not_awaited()


@pytest.mark.parametrize(
    ("event", "status"),
    [("ConnectedCallback", "connected"), ("DisconnectedCallback", "disconnected")],
)
def test_evento_de_conexao_atualiza_o_status(
    webhook_client, fake_session, arq_pool, event, status
) -> None:
    number = _omni_number(status="?")
    fake_session.scalar.side_effect = [number]
    body, headers = _signed({"type": event, "connected": event == "ConnectedCallback"})

    response = webhook_client.post(WEBHOOK_PATH, content=body, headers=headers)

    assert response.status_code == 200
    assert number.status == status
    fake_session.commit.assert_awaited()
    arq_pool.enqueue_job.assert_not_awaited()


# --- cadastro pelo admin ---------------------------------------------------------

TENANT_ID = uuid.uuid4()
OMNI_BODY = {"channel_id": "canal-1", "channel_token": "tok-canal"}


@pytest.fixture
def admin_session():
    mock = AsyncMock()
    mock.add = MagicMock()
    mock.get = AsyncMock(return_value=SimpleNamespace(id=TENANT_ID))
    mock.scalar.return_value = None
    return mock


@pytest.fixture
def admin_client(admin_session, monkeypatch):
    import app.api.v1.platform_admin.whatsapp as admin_module

    monkeypatch.setattr(admin_module.settings, "omni_enabled", True)

    async def override_admin():
        return PlatformAdminContext(admin_id=uuid.uuid4(), role="superadmin")

    async def override_session():
        yield admin_session

    app.dependency_overrides[get_current_platform_admin] = override_admin
    app.dependency_overrides[get_system_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def omni_mocks(monkeypatch):
    mocks = {
        "get_channel": AsyncMock(
            return_value={
                "whatsappConnected": True,
                "phoneNumberConnected": "5511977776666",
                "businessId": "waba-9",
            }
        ),
        "create_webhook": AsyncMock(return_value=("wh-1", "segredo-hmac")),
        "delete_webhook": AsyncMock(),
    }
    monkeypatch.setattr(omni_connection_module, "get_channel", mocks["get_channel"])
    monkeypatch.setattr(omni_connection_module, "create_channel_webhook", mocks["create_webhook"])
    monkeypatch.setattr(omni_connection_module, "delete_channel_webhook", mocks["delete_webhook"])
    monkeypatch.setattr(omni_connection_module, "encrypt_access_token", lambda v: f"cifrado:{v}")
    monkeypatch.setattr(
        omni_connection_module.settings, "api_public_url", "https://api.exemplo.com.br"
    )
    return mocks


def _added_number(session):
    return next(
        call.args[0]
        for call in session.add.call_args_list
        if getattr(call.args[0], "provider", None) == "omni"
    )


def test_admin_cadastra_canal_e_cria_webhook(admin_client, admin_session, omni_mocks) -> None:
    response = admin_client.post(
        f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp/omni", json=OMNI_BODY
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "omni"
    assert body["managed_by_advoxs"] is True
    assert body["status"] == "connected"
    number = _added_number(admin_session)
    url = omni_mocks["create_webhook"].await_args.args[1]
    assert (
        url == f"https://api.exemplo.com.br/api/v1/webhooks/omni/{number.omni_webhook_path_secret}"
    )
    assert number.omni_channel_id == "canal-1"
    assert number.omni_channel_token_encrypted == "cifrado:tok-canal"
    assert number.omni_webhook_signing_secret_encrypted == "cifrado:segredo-hmac"
    assert number.waba_id == "waba-9"
    assert number.display_phone_number == "5511977776666"


def test_sem_enterprise_usa_os_dados_informados(admin_client, admin_session, omni_mocks) -> None:
    omni_mocks["get_channel"].return_value = None

    response = admin_client.post(
        f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp/omni",
        json={**OMNI_BODY, "display_phone_number": "5511900001111", "waba_id": "waba-x"},
    )

    assert response.status_code == 200
    number = _added_number(admin_session)
    assert number.display_phone_number == "5511900001111"
    assert number.waba_id == "waba-x"


def test_canal_recusado_pelo_omni_retorna_400(admin_client, omni_mocks) -> None:
    omni_mocks["get_channel"].side_effect = OmniApiError("Channel not found", 404)

    response = admin_client.post(
        f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp/omni", json=OMNI_BODY
    )

    assert response.status_code == 400
    omni_mocks["create_webhook"].assert_not_awaited()


def test_integracao_desligada_retorna_409(admin_client, monkeypatch, omni_mocks) -> None:
    import app.api.v1.platform_admin.whatsapp as admin_module

    monkeypatch.setattr(admin_module.settings, "omni_enabled", False)

    response = admin_client.post(
        f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp/omni", json=OMNI_BODY
    )

    assert response.status_code == 409
    omni_mocks["get_channel"].assert_not_awaited()


def test_reconexao_remove_o_webhook_antigo(admin_client, admin_session, omni_mocks) -> None:
    existente = SimpleNamespace(
        tenant_id=TENANT_ID,
        provider="omni",
        omni_channel_id="canal-antigo",
        omni_webhook_id="wh-antigo",
    )
    # 1ª consulta: o número do escritório; as seguintes (pedido pendente): nada.
    admin_session.scalar.side_effect = [existente, None, None]

    admin_client.post(f"/api/v1/platform-admin/tenants/{TENANT_ID}/whatsapp/omni", json=OMNI_BODY)

    omni_mocks["delete_webhook"].assert_awaited_once_with("canal-antigo", "wh-antigo")
    assert existente.omni_channel_id == "canal-1"


# --- envio avulso (takeover, avisos) ---------------------------------------------


async def test_texto_avulso_sai_pelo_omni(monkeypatch) -> None:
    enviar = AsyncMock()
    monkeypatch.setattr(outbound_module, "send_zapi_text_message", enviar)
    monkeypatch.setattr(outbound_module, "decrypt_access_token", lambda v: "tok-claro")
    number = SimpleNamespace(
        provider="omni", omni_channel_id="canal-1", omni_channel_token_encrypted="cifrado"
    )

    await outbound_module.send_text_to_contact(number, "5511888888888", "Olá")

    kwargs = enviar.await_args.kwargs
    assert kwargs["instance_id"] == "canal-1"
    assert kwargs["token"] == "tok-claro"
    assert kwargs["omni"] is True


# --- modelos do follow-up --------------------------------------------------------


async def test_modelos_do_omni_no_formato_da_meta(monkeypatch) -> None:
    listar = AsyncMock(
        return_value=[
            {
                "name": "lembrete",
                "status": "APPROVED",
                "category": "UTILITY",
                "language": "pt_BR",
                "parameter_format": "POSITIONAL",
                "components": [{"type": "BODY", "text": "Olá {{1}}"}],
            }
        ]
    )
    monkeypatch.setattr(follow_ups_module, "list_waba_templates", listar)
    number = SimpleNamespace(provider="omni", waba_id="waba-9")

    templates = await follow_ups_module._meta_templates(number)

    listar.assert_awaited_once_with("waba-9")
    assert templates[0]["name"] == "lembrete"
    assert templates[0]["supported"] is True


async def test_omni_sem_waba_explica_o_que_falta() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as erro:
        await follow_ups_module._meta_templates(SimpleNamespace(provider="omni", waba_id=None))
    assert erro.value.status_code == 409


# --- configuração de produção ----------------------------------------------------


def _prod(**extra) -> dict:
    return {"APP_ENV": "production", "ENFORCE_PRODUCTION_CONFIG": "true", **extra}


def _complain(values: dict, service: str) -> str:
    try:
        validate_config(values, service)
    except RuntimeError as exc:
        return str(exc)
    return ""


def test_omni_desligado_por_padrao_nao_exige_chave() -> None:
    assert "OMNI_SECRET_KEY" not in _complain(_prod(), "worker")


@pytest.mark.parametrize(("service", "exige"), [("api", True), ("worker", True), ("agents", False)])
def test_omni_ligado_exige_chave_no_api_e_no_worker(service, exige) -> None:
    assert ("OMNI_SECRET_KEY" in _complain(_prod(OMNI_ENABLED="true"), service)) is exige
