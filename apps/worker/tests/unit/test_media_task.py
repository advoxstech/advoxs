import logging
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from arq.worker import Retry

from app.clients.media import MediaDownloadError, MediaTooLargeError
from app.clients.transcription import TranscriptionError, TranscriptionResult
from app.config import settings
from app.tasks import media as media_task
from app.tasks.inbound_context import InboundContext
from app.tasks.media import (
    failure_note,
    prepare_media,
    record_media_skipped,
    transcript_note,
    transcription_payer,
)

TENANT_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())
MESSAGE_ID = str(uuid.uuid4())
SECRET_TEXT = "Meu CPF é 123.456.789-00"
PRICING = SimpleNamespace(id=uuid.uuid4(), transcription_credits_per_minute=Decimal("1.0"))


def _inbound(**overrides) -> InboundContext:
    values = dict(
        conversation_state="agent",
        contact_phone_number="5511888888888",
        message_content="",
        whatsapp_provider="meta",
        phone_number_id="PNID",
        access_token_encrypted="cifrado",
        zapi_instance_id=None,
        zapi_instance_token_encrypted=None,
        zapi_client_token_encrypted=None,
        credit_balance=Decimal(100),
        end_customer_billing_enabled=False,
        end_customer_balance=Decimal(0),
        end_customer_packages=[],
        agents=[],
        media_url="media-id-1",
        media_type="audio/ogg; codecs=opus",
    )
    values.update(overrides)
    return InboundContext(**values)


def _ctx(job_try: int = 1) -> dict:
    session = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return {"session_factory": factory, "job_try": job_try}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(settings, "audio_transcription_enabled", True)
    mocks = {
        "load_row": AsyncMock(return_value=None),
        "save_row": AsyncMock(),
        "download_meta": AsyncMock(return_value=b"OggS-bytes"),
        "download_zapi": AsyncMock(return_value=b"OggS-bytes"),
        "detect": MagicMock(return_value="ogg"),
        "duration": MagicMock(return_value=30.0),
        "transcribe": AsyncMock(return_value=TranscriptionResult(text=SECRET_TEXT)),
        "pricing": AsyncMock(return_value=PRICING),
        "debit_tenant": AsyncMock(return_value=(False, Decimal(1))),
        "debit_customer": AsyncMock(return_value=Decimal(1)),
        "decrypt": MagicMock(return_value="token-claro"),
    }
    monkeypatch.setattr(media_task, "_load_media_row", mocks["load_row"])
    monkeypatch.setattr(media_task, "_save_media_row", mocks["save_row"])
    monkeypatch.setattr(media_task, "download_meta_media", mocks["download_meta"])
    monkeypatch.setattr(media_task, "download_zapi_media", mocks["download_zapi"])
    monkeypatch.setattr(media_task, "detect_audio_format", mocks["detect"])
    monkeypatch.setattr(media_task, "read_duration_seconds", mocks["duration"])
    monkeypatch.setattr(media_task, "transcribe", mocks["transcribe"])
    monkeypatch.setattr(media_task, "get_current_pricing_config", mocks["pricing"])
    monkeypatch.setattr(media_task, "_debitar_creditos", mocks["debit_tenant"])
    monkeypatch.setattr(media_task, "_debitar_creditos_cliente_final", mocks["debit_customer"])
    monkeypatch.setattr(media_task, "decrypt_access_token", mocks["decrypt"])
    return mocks


async def _run(inbound=None, ctx=None):
    return await prepare_media(
        ctx or _ctx(),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
        inbound=inbound or _inbound(),
    )


def _final_save(patched) -> dict:
    return patched["save_row"].await_args_list[-1].kwargs


async def test_desligado_nao_faz_nada(patched, monkeypatch) -> None:
    monkeypatch.setattr(settings, "audio_transcription_enabled", False)
    assert await _run() is None
    patched["load_row"].assert_not_awaited()


async def test_sem_audio_nao_faz_nada(patched) -> None:
    assert await _run(_inbound(media_type="application/pdf")) is None
    assert await _run(_inbound(media_url=None)) is None
    patched["load_row"].assert_not_awaited()


async def test_sucesso_grava_done_cobra_tenant_e_devolve_nota(patched) -> None:
    note = await _run()

    assert note == transcript_note(SECRET_TEXT, uncertain=False)
    patched["download_meta"].assert_awaited_once_with(
        "media-id-1", "token-claro", settings.audio_max_bytes
    )
    patched["transcribe"].assert_awaited_once()
    assert patched["transcribe"].await_args.args[1] == f"audio-{MESSAGE_ID}.ogg"
    debit = patched["debit_tenant"].await_args
    assert debit.args[4] == Decimal(1)  # 30 s × 1 crédito/min = 0,5 → 1 (HALF_UP)
    assert debit.kwargs["description"] == "Transcrição de áudio"
    final = _final_save(patched)
    assert final["status"] == "done"
    assert final["text"] == SECRET_TEXT
    assert final["attempts"] == 1
    assert final["credits_consumed"] == Decimal(1)
    assert final["cost_usd"] == Decimal("0.003000")


async def test_zapi_usa_client_token(patched) -> None:
    inbound = _inbound(
        whatsapp_provider="zapi",
        media_url="https://zapi/x.ogg",
        zapi_client_token_encrypted="cifrado-zapi",
        access_token_encrypted=None,
    )
    await _run(inbound)
    patched["download_zapi"].assert_awaited_once_with(
        "https://zapi/x.ogg", "token-claro", settings.audio_max_bytes
    )


async def test_omni_baixa_com_secret_key_da_plataforma(patched) -> None:
    inbound = _inbound(
        whatsapp_provider="omni",
        media_url="https://media.omni.z-api.io/x.ogg",
        access_token_encrypted=None,
    )
    await _run(inbound)
    patched["download_zapi"].assert_awaited_once_with(
        "https://media.omni.z-api.io/x.ogg", None, settings.audio_max_bytes, omni=True
    )
    patched["decrypt"].assert_not_called()


def test_teto_de_audio_respeita_limite_da_openai() -> None:
    assert settings.audio_max_bytes <= 25 * 1024 * 1024
    assert "25 MB" in failure_note("too_large")


async def test_turno_custeado_pelo_cliente_final_debita_cliente(patched) -> None:
    await _run(_inbound(end_customer_billing_enabled=True, end_customer_balance=Decimal(5)))
    patched["debit_customer"].assert_awaited_once()
    patched["debit_tenant"].assert_not_awaited()


async def test_assinatura_ativa_nao_debita(patched) -> None:
    await _run(_inbound(end_customer_has_active_subscription=True))
    patched["debit_customer"].assert_not_awaited()
    patched["debit_tenant"].assert_not_awaited()
    assert _final_save(patched)["status"] == "done"


async def test_modo_humano_sempre_cobra_tenant(patched) -> None:
    await _run(
        _inbound(
            conversation_state="human",
            end_customer_billing_enabled=True,
            end_customer_balance=Decimal(5),
        )
    )
    patched["debit_tenant"].assert_awaited_once()
    patched["debit_customer"].assert_not_awaited()


async def test_sem_saldo_registra_skipped_sem_baixar(patched) -> None:
    note = await _run(_inbound(conversation_state="human", credit_balance=Decimal(0)))
    assert note is None
    patched["download_meta"].assert_not_awaited()
    final = _final_save(patched)
    assert final["status"] == "skipped"
    assert final["error_code"] == "no_credits"


async def test_done_reaproveita_sem_baixar_nem_cobrar(patched) -> None:
    patched["load_row"].return_value = SimpleNamespace(
        status="done",
        error_code=None,
        attempts=1,
        text=SECRET_TEXT,
        uncertain_spans=[{"text": "x", "confidence": 0.2}],
    )
    note = await _run()
    assert note == transcript_note(SECRET_TEXT, uncertain=True)
    patched["download_meta"].assert_not_awaited()
    patched["transcribe"].assert_not_awaited()
    patched["debit_tenant"].assert_not_awaited()
    patched["save_row"].assert_not_awaited()


async def test_falha_permanente_registrada_reaproveita_nota(patched) -> None:
    patched["load_row"].return_value = SimpleNamespace(
        status="failed", error_code="too_long", attempts=1, text=None, uncertain_spans=None
    )
    assert await _run() == failure_note("too_long")
    patched["download_meta"].assert_not_awaited()


async def test_processing_orfao_conta_como_nova_tentativa(patched) -> None:
    patched["load_row"].return_value = SimpleNamespace(
        status="processing", error_code=None, attempts=2, text=None, uncertain_spans=None
    )
    await _run()
    assert _final_save(patched)["attempts"] == 3
    assert _final_save(patched)["status"] == "done"


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        (lambda m: setattr(m["detect"], "return_value", None), "invalid_format"),
        (lambda m: setattr(m["duration"], "return_value", None), "invalid_format"),
        (lambda m: setattr(m["duration"], "return_value", 601.0), "too_long"),
        (
            lambda m: setattr(m["download_meta"], "side_effect", MediaTooLargeError("x")),
            "too_large",
        ),
        (
            lambda m: setattr(m["transcribe"], "return_value", TranscriptionResult(text=" ... ")),
            "silence",
        ),
        (
            lambda m: setattr(m["transcribe"], "side_effect", TranscriptionError("invalid_format")),
            "invalid_format",
        ),
    ],
)
async def test_falhas_permanentes_nao_retentam_nem_cobram(patched, setup, code) -> None:
    setup(patched)
    note = await _run()
    assert note == failure_note(code)
    final = _final_save(patched)
    assert final["status"] == "failed"
    assert final["error_code"] == code
    patched["debit_tenant"].assert_not_awaited()


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        (
            lambda m: setattr(m["download_meta"], "side_effect", MediaDownloadError("x")),
            "download_failed",
        ),
        (
            lambda m: setattr(m["transcribe"], "side_effect", TranscriptionError("timeout")),
            "timeout",
        ),
        (
            lambda m: setattr(
                m["transcribe"], "side_effect", TranscriptionError("provider_unavailable")
            ),
            "provider_unavailable",
        ),
    ],
)
async def test_falha_transitoria_reagenda_ate_o_limite(patched, setup, code) -> None:
    setup(patched)
    with pytest.raises(Retry):
        await _run()
    assert _final_save(patched)["error_code"] == code

    patched["load_row"].return_value = SimpleNamespace(
        status="failed", error_code=code, attempts=2, text=None, uncertain_spans=None
    )
    note = await _run()  # 3ª tentativa: segue com nota, sem Retry
    assert note == failure_note(code)
    assert _final_save(patched)["attempts"] == 3


async def test_record_media_skipped_so_para_audio(patched) -> None:
    await record_media_skipped(
        _ctx(),
        tenant_id=TENANT_ID,
        message_id=MESSAGE_ID,
        inbound=_inbound(media_type="application/pdf"),
        error_code="billing_gate",
    )
    patched["save_row"].assert_not_awaited()
    await record_media_skipped(
        _ctx(),
        tenant_id=TENANT_ID,
        message_id=MESSAGE_ID,
        inbound=_inbound(),
        error_code="billing_gate",
    )
    assert _final_save(patched)["status"] == "skipped"
    assert _final_save(patched)["error_code"] == "billing_gate"


def test_payer() -> None:
    assert transcription_payer(_inbound()) == "tenant"
    assert transcription_payer(_inbound(credit_balance=Decimal(0))) is None
    assert transcription_payer(_inbound(end_customer_has_active_subscription=True)) == "none"
    assert (
        transcription_payer(
            _inbound(end_customer_billing_enabled=True, end_customer_balance=Decimal(1))
        )
        == "end_customer"
    )
    assert (
        transcription_payer(
            _inbound(
                end_customer_billing_enabled=True,
                end_customer_balance=Decimal(1),
                end_customer_billing_exempt=True,
            )
        )
        == "tenant"
    )
    assert transcription_payer(_inbound(human_only=True, conversation_state="human")) == "tenant"


def test_notas() -> None:
    assert transcript_note("oi", False) == "[Áudio do cliente, transcrito automaticamente]: oi"
    assert "confirme com o cliente" in transcript_note("oi [?x?]", True)
    assert "10 minutos" in failure_note("too_long")
    assert failure_note("silence").startswith("[O cliente enviou um áudio que não pôde ser")


async def test_logs_nao_contem_texto(patched, caplog) -> None:
    caplog.set_level(logging.INFO)
    await _run()
    patched["transcribe"].side_effect = TranscriptionError("invalid_format")
    await _run()
    assert SECRET_TEXT not in caplog.text
    assert "123.456" not in caplog.text
    assert "media-id-1" not in caplog.text
    assert "status=done" in caplog.text


async def test_skipped_nunca_sobrescreve_done() -> None:
    session = AsyncMock()
    await media_task._save_media_row(
        session,
        TENANT_ID,
        uuid.UUID(MESSAGE_ID),
        only_if_not_done=True,
        status="skipped",
        error_code="no_credits",
    )
    sql = str(session.execute.await_args.args[0].compile())
    assert "message_media.status != " in sql
    session.execute.reset_mock()
    await media_task._save_media_row(session, TENANT_ID, uuid.UUID(MESSAGE_ID), status="done")
    assert "message_media.status != " not in str(session.execute.await_args.args[0].compile())


async def test_done_concorrente_nao_cobra_de_novo(patched) -> None:
    done = SimpleNamespace(
        status="done", error_code=None, attempts=1, text=SECRET_TEXT, uncertain_spans=None
    )
    patched["load_row"].side_effect = [None, done]
    note = await _run()
    assert note == transcript_note(SECRET_TEXT, uncertain=False)
    patched["debit_tenant"].assert_not_awaited()
    assert all(c.kwargs.get("status") != "done" for c in patched["save_row"].await_args_list)


async def test_ultima_tentativa_do_job_nao_reagenda(patched) -> None:
    patched["transcribe"].side_effect = TranscriptionError("timeout")
    note = await _run(ctx=_ctx(job_try=5))
    assert note == failure_note("timeout")
