import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.clients.whatsapp as whatsapp_client
from app.clients.whatsapp import WhatsAppTemplateError, send_template_message
from app.clients.zapi import ZApiApiError, ZApiNetworkError
from app.follow_ups import template_components
from app.tasks import follow_ups

TENANT_ID = str(uuid.uuid4())
BATCH_ID = str(uuid.uuid4())

TEMPLATE = {
    "name": "retomada",
    "language": "pt_BR",
    "parameter_format": "POSITIONAL",
    "header_text": None,
    "body_text": "Olá, {{1}}. Aqui é do escritório {{2}}.",
    "footer_text": None,
    "param_names": ["1", "2"],
}
PARAMS = [
    {"source": "contact_name", "value": "cliente"},
    {"source": "text", "value": "Silva Advogados"},
]


def _meta_batch() -> SimpleNamespace:
    return SimpleNamespace(
        provider="meta",
        template=TEMPLATE,
        params=PARAMS,
        text=None,
        created_by_user_id=uuid.uuid4(),
        created_by_name="Ana",
    )


def _number(provider: str = "meta") -> SimpleNamespace:
    return SimpleNamespace(
        provider=provider,
        phone_number_id="PNID",
        access_token_encrypted="cifrado",
        zapi_instance_id="inst-1",
        zapi_instance_token_encrypted="token",
        zapi_client_token_encrypted="client",
    )


def _recipient(attempts: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), phone="5579999990000", contact_name="Maria", attempts=attempts
    )


class TestTemplateComponents:
    def test_posicional(self) -> None:
        assert template_components(TEMPLATE, ["Maria", "Silva"]) == [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": "Maria"},
                    {"type": "text", "text": "Silva"},
                ],
            }
        ]

    def test_nomeado_leva_parameter_name(self) -> None:
        template = {**TEMPLATE, "parameter_format": "NAMED", "param_names": ["nome"]}

        parameters = template_components(template, ["Maria"])[0]["parameters"]

        assert parameters == [{"type": "text", "text": "Maria", "parameter_name": "nome"}]

    def test_sem_variaveis_nao_manda_componentes(self) -> None:
        assert template_components({**TEMPLATE, "param_names": []}, []) == []


def _mock_post(monkeypatch, status_code: int, body: dict) -> AsyncMock:
    response = MagicMock()
    response.status_code = status_code
    response.is_error = status_code >= 400
    response.json.return_value = body
    client = AsyncMock()
    client.post.return_value = response
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(whatsapp_client.httpx, "AsyncClient", MagicMock(return_value=cm))
    return client


class TestSendTemplateMessage:
    async def test_monta_payload_de_modelo(self, monkeypatch) -> None:
        client = _mock_post(monkeypatch, 200, {})

        await send_template_message(
            "PNID", "token", "5579999990000", "retomada", "pt_BR", [{"type": "body"}]
        )

        payload = client.post.call_args.kwargs["json"]
        assert payload["type"] == "template"
        assert payload["template"] == {
            "name": "retomada",
            "language": {"code": "pt_BR"},
            "components": [{"type": "body"}],
        }

    @pytest.mark.parametrize(
        ("status_code", "code", "retryable"),
        [(400, 132015, False), (400, 131026, False), (429, 130429, True), (500, 1, True)],
    )
    async def test_classifica_erros(self, monkeypatch, status_code, code, retryable) -> None:
        _mock_post(monkeypatch, status_code, {"error": {"code": code, "message": "falhou"}})

        with pytest.raises(WhatsAppTemplateError) as error:
            await send_template_message("PNID", "token", "5579", "retomada", "pt_BR", [])

        assert error.value.retryable is retryable

    async def test_codigo_conhecido_vira_mensagem_em_portugues(self, monkeypatch) -> None:
        _mock_post(monkeypatch, 400, {"error": {"code": 132015, "message": "Paused"}})

        with pytest.raises(WhatsAppTemplateError, match="pausado"):
            await send_template_message("PNID", "token", "5579", "retomada", "pt_BR", [])


class TestSend:
    async def test_meta_envia_modelo_e_devolve_texto_renderizado(self, monkeypatch) -> None:
        send = AsyncMock()
        monkeypatch.setattr(follow_ups, "send_template_message", send)
        monkeypatch.setattr(follow_ups, "decrypt_access_token", lambda value: f"claro:{value}")

        content = await follow_ups._send(_meta_batch(), _number(), "5579999990000", "")

        assert content == "Olá, cliente. Aqui é do escritório Silva Advogados."
        kwargs = send.call_args.kwargs
        assert kwargs["access_token"] == "claro:cifrado"
        assert kwargs["components"][0]["parameters"][0]["text"] == "cliente"

    async def test_zapi_envia_texto_com_nome(self, monkeypatch) -> None:
        send = AsyncMock()
        monkeypatch.setattr(follow_ups, "send_zapi_text_message", send)
        monkeypatch.setattr(follow_ups, "decrypt_access_token", lambda value: f"claro:{value}")
        batch = SimpleNamespace(provider="zapi", text="Oi {{nome}}!")

        content = await follow_ups._send(batch, _number("zapi"), "5579999990000", "Maria")

        assert content == "Oi Maria!"
        assert send.call_args.kwargs["client_token"] == "claro:client"

    @pytest.mark.parametrize(
        ("error", "retryable"),
        [(ZApiNetworkError("rede"), True), (ZApiApiError("número inválido"), False)],
    )
    async def test_zapi_classifica_erros(self, monkeypatch, error, retryable) -> None:
        monkeypatch.setattr(follow_ups, "send_zapi_text_message", AsyncMock(side_effect=error))
        monkeypatch.setattr(follow_ups, "decrypt_access_token", lambda value: value)

        with pytest.raises(follow_ups.FollowUpSendError) as raised:
            await follow_ups._send(
                SimpleNamespace(provider="zapi", text="Oi"), _number("zapi"), "5579", ""
            )

        assert raised.value.retryable is retryable

    async def test_sem_numero_conectado_falha_sem_nova_tentativa(self) -> None:
        with pytest.raises(follow_ups.FollowUpSendError) as raised:
            await follow_ups._send(_meta_batch(), None, "5579", "")

        assert raised.value.retryable is False

    async def test_numero_trocado_de_provedor_nao_envia(self) -> None:
        with pytest.raises(follow_ups.FollowUpSendError):
            await follow_ups._send(_meta_batch(), _number("zapi"), "5579", "")


@pytest.fixture
def deliver_mocks(monkeypatch):
    @asynccontextmanager
    async def fake_session(*args):
        yield AsyncMock()

    mocks = {
        "find": AsyncMock(return_value=None),
        "send": AsyncMock(return_value="Olá, Maria."),
        "record": AsyncMock(),
        "finish": AsyncMock(),
        "sync": AsyncMock(),
    }
    monkeypatch.setattr(follow_ups, "open_tenant_session", fake_session)
    monkeypatch.setattr(follow_ups, "_find_conversation", mocks["find"])
    monkeypatch.setattr(follow_ups, "_send", mocks["send"])
    monkeypatch.setattr(follow_ups, "_record_message", mocks["record"])
    monkeypatch.setattr(follow_ups, "_finish", mocks["finish"])
    monkeypatch.setattr(follow_ups, "sync_context_to_agents", mocks["sync"])
    return mocks


CTX = {"session_factory": object(), "http": object()}


class TestDeliver:
    async def test_sucesso_registra_e_sincroniza_contexto(self, deliver_mocks) -> None:
        recipient = _recipient()

        await follow_ups._deliver(CTX, TENANT_ID, _meta_batch(), _number(), recipient)

        deliver_mocks["record"].assert_awaited_once()
        assert deliver_mocks["record"].call_args.args[4] == "Olá, Maria."
        sync = deliver_mocks["sync"].call_args.kwargs
        assert sync["role"] == "attendant" and sync["content"] == "Olá, Maria."
        deliver_mocks["finish"].assert_not_awaited()

    async def test_conversa_em_exclusao_e_pulada_sem_enviar(self, deliver_mocks) -> None:
        deliver_mocks["find"].return_value = SimpleNamespace(
            id=uuid.uuid4(), deletion_requested_at="2026-11-01"
        )

        await follow_ups._deliver(CTX, TENANT_ID, _meta_batch(), _number(), _recipient())

        deliver_mocks["send"].assert_not_awaited()
        assert deliver_mocks["finish"].call_args.kwargs["status"] == "skipped"

    async def test_falha_temporaria_volta_para_fila(self, deliver_mocks) -> None:
        deliver_mocks["send"].side_effect = follow_ups.FollowUpSendError("rede", retryable=True)

        await follow_ups._deliver(CTX, TENANT_ID, _meta_batch(), _number(), _recipient(1))

        kwargs = deliver_mocks["finish"].call_args.kwargs
        assert kwargs["status"] == "pending" and kwargs["available_at"] is not None
        deliver_mocks["record"].assert_not_awaited()

    async def test_ultima_tentativa_vira_falha(self, deliver_mocks) -> None:
        deliver_mocks["send"].side_effect = follow_ups.FollowUpSendError("rede", retryable=True)

        await follow_ups._deliver(
            CTX, TENANT_ID, _meta_batch(), _number(), _recipient(follow_ups.MAX_ATTEMPTS)
        )

        assert deliver_mocks["finish"].call_args.kwargs["status"] == "failed"

    async def test_recusa_definitiva_nao_tenta_de_novo(self, deliver_mocks) -> None:
        deliver_mocks["send"].side_effect = follow_ups.FollowUpSendError(
            "Modelo pausado.", retryable=False
        )

        await follow_ups._deliver(CTX, TENANT_ID, _meta_batch(), _number(), _recipient(1))

        kwargs = deliver_mocks["finish"].call_args.kwargs
        assert kwargs["status"] == "failed" and kwargs["error"] == "Modelo pausado."

    async def test_falha_ao_registrar_nunca_reenvia(self, deliver_mocks) -> None:
        deliver_mocks["record"].side_effect = RuntimeError("banco")

        await follow_ups._deliver(CTX, TENANT_ID, _meta_batch(), _number(), _recipient())

        deliver_mocks["send"].assert_awaited_once()
        assert deliver_mocks["finish"].call_args.kwargs["status"] == "sent"
        deliver_mocks["sync"].assert_not_awaited()

    async def test_falha_ao_sincronizar_contexto_nao_derruba_envio(self, deliver_mocks) -> None:
        deliver_mocks["sync"].side_effect = RuntimeError("agents fora")

        await follow_ups._deliver(CTX, TENANT_ID, _meta_batch(), _number(), _recipient())

        deliver_mocks["record"].assert_awaited_once()
        deliver_mocks["finish"].assert_not_awaited()


class TestDeliverBatch:
    async def test_envia_um_por_vez_com_intervalo(self, monkeypatch) -> None:
        @asynccontextmanager
        async def fake_session(*args):
            session = AsyncMock()
            first, second = MagicMock(), MagicMock()
            first.one_or_none.return_value = _meta_batch()
            second.one_or_none.return_value = _number()
            session.execute.side_effect = [first, second]
            yield session

        claims = [_recipient(), _recipient(), None]
        deliver = AsyncMock()
        sleep = AsyncMock()
        monkeypatch.setattr(follow_ups, "open_tenant_session", fake_session)
        monkeypatch.setattr(follow_ups, "_claim_next", AsyncMock(side_effect=claims))
        monkeypatch.setattr(follow_ups, "_deliver", deliver)
        monkeypatch.setattr(follow_ups.asyncio, "sleep", sleep)

        await follow_ups.deliver_follow_up_batch(CTX, TENANT_ID, BATCH_ID)

        assert deliver.await_count == 2
        sleep.assert_awaited_once_with(follow_ups.SEND_INTERVAL_SECONDS)

    async def test_lote_inexistente_nao_faz_nada(self, monkeypatch) -> None:
        @asynccontextmanager
        async def fake_session(*args):
            session = AsyncMock()
            empty = MagicMock()
            empty.one_or_none.return_value = None
            session.execute.return_value = empty
            yield session

        claim = AsyncMock()
        monkeypatch.setattr(follow_ups, "open_tenant_session", fake_session)
        monkeypatch.setattr(follow_ups, "_claim_next", claim)

        await follow_ups.deliver_follow_up_batch(CTX, TENANT_ID, BATCH_ID)

        claim.assert_not_awaited()


async def test_recuperacao_reenfileira_com_id_estavel(monkeypatch) -> None:
    batch_id = uuid.uuid4()

    @asynccontextmanager
    async def fake_system_session(*args):
        session = AsyncMock()
        rows = MagicMock()
        rows.all.return_value = [SimpleNamespace(id=batch_id, tenant_id=uuid.UUID(TENANT_ID))]
        session.execute.side_effect = [MagicMock(), MagicMock(), rows, MagicMock()]
        yield session

    redis = AsyncMock()
    monkeypatch.setattr(follow_ups, "open_system_session", fake_system_session)

    await follow_ups.recover_follow_up_batches({"system_session_factory": object(), "redis": redis})

    redis.enqueue_job.assert_awaited_once_with(
        "deliver_follow_up_batch",
        tenant_id=TENANT_ID,
        batch_id=str(batch_id),
        _job_id=f"follow-up:{batch_id}",
    )


async def test_erro_inesperado_conta_como_tentativa(deliver_mocks) -> None:
    deliver_mocks["send"].side_effect = ValueError("token ilegível")

    await follow_ups._deliver(CTX, TENANT_ID, _meta_batch(), _number(), _recipient(1))

    kwargs = deliver_mocks["finish"].call_args.kwargs
    assert kwargs["status"] == "pending"
    assert "inesperado" in kwargs["error"]
