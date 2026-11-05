"""Processamento de mídia recebida do contato antes do turno do agente.

Hoje: transcrição de áudio. Estado e resultado em `message_media` (migration
0049), que é também o contrato do OCR (kind=document|image). Idempotente por
message_id: `done` nunca é refeito nem cobrado de novo; `processing` órfão
(worker morto) conta como nova tentativa. Ver
docs/superpowers/specs/2026-11-04-midia-audio-transcricao-design.md."""

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from arq.worker import Retry
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import tables
from app.audio_validation import detect_audio_format, is_audio, read_duration_seconds
from app.clients.media import (
    MediaDownloadError,
    MediaTooLargeError,
    download_meta_media,
    download_zapi_media,
)
from app.clients.transcription import TranscriptionError, transcribe
from app.config import settings
from app.crypto import decrypt_access_token
from app.db import open_tenant_session
from app.pricing import get_current_pricing_config
from app.tasks.inbound_context import InboundContext
from app.tasks.wallet import _debitar_creditos, _debitar_creditos_cliente_final

logger = logging.getLogger(__name__)

# Espelha messages.MAX_TRIES (default do Arq); não importado para evitar ciclo.
MAX_TRIES = 5
TRANSIENT_ERRORS = {"download_failed", "timeout", "provider_unavailable"}
_DESCRIPTION = "Transcrição de áudio"


class _MediaFailure(Exception):
    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


def _failure_reasons() -> dict[str, str]:
    minutes = settings.audio_max_duration_seconds // 60
    return {
        "download_failed": "não foi possível baixar o arquivo",
        "timeout": "o serviço de transcrição demorou demais para responder",
        "provider_unavailable": "o serviço de transcrição está indisponível",
        "invalid_format": "formato de áudio não suportado",
        "too_large": "o arquivo passa de 20 MB",
        "too_long": f"o áudio passa de {minutes} minutos",
        "silence": "sem fala detectada",
    }


def transcript_note(text: str, uncertain: bool) -> str:
    note = f"[Áudio do cliente, transcrito automaticamente]: {text}"
    if uncertain:
        note += (
            "\n[Trechos entre [? ?] ficaram incertos na transcrição: confirme com o cliente "
            "antes de usá-los e não complete fatos.]"
        )
    return note


def failure_note(error_code: str) -> str:
    reason = _failure_reasons().get(error_code, "erro no processamento")
    return (
        f"[O cliente enviou um áudio que não pôde ser transcrito ({reason}). "
        "Peça que reenvie o áudio ou escreva a mensagem.]"
    )


def transcription_payer(inbound: InboundContext) -> str | None:
    """Quem paga a transcrição; None = não transcrever (sem saldo)."""
    if inbound.conversation_state == "agent" and not inbound.human_only:
        if inbound.end_customer_has_active_subscription:
            return "none"
        if (
            not inbound.end_customer_billing_exempt
            and inbound.end_customer_billing_enabled
            and inbound.end_customer_balance > 0
        ):
            return "end_customer"
    return "tenant" if inbound.credit_balance > 0 else None


def _applies(inbound: InboundContext) -> bool:
    return (
        settings.audio_transcription_enabled
        and bool(inbound.media_url)
        and is_audio(inbound.media_type)
    )


async def _load_media_row(session: AsyncSession, message_id: uuid.UUID):
    mm = tables.message_media
    return (
        await session.execute(
            select(mm.c.status, mm.c.error_code, mm.c.attempts, mm.c.text, mm.c.uncertain_spans)
            .where(mm.c.message_id == message_id)
            .with_for_update()
        )
    ).one_or_none()


async def _save_media_row(
    session: AsyncSession,
    tenant_id: str,
    message_id: uuid.UUID,
    only_if_not_done: bool = False,
    **values,
) -> None:
    values["updated_at"] = datetime.now(UTC)
    statement = pg_insert(tables.message_media).values(
        message_id=message_id, tenant_id=uuid.UUID(tenant_id), kind="audio", **values
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[tables.message_media.c.message_id],
            set_=values,
            where=tables.message_media.c.status != "done" if only_if_not_done else None,
        )
    )


def _log(tenant_id, conversation_id, message_id, status, error_code, attempts, **metrics) -> None:
    logger.info(
        "Mídia processada | tenant=%s conversation=%s message=%s kind=audio status=%s "
        "error_code=%s attempts=%s duration_s=%s download_ms=%s transcribe_ms=%s cost_usd=%s",
        tenant_id,
        conversation_id,
        message_id,
        status,
        error_code,
        attempts,
        metrics.get("duration_s"),
        metrics.get("download_ms"),
        metrics.get("transcribe_ms"),
        metrics.get("cost_usd"),
    )


async def record_media_skipped(
    ctx: dict, *, tenant_id: str, message_id: str, inbound: InboundContext, error_code: str
) -> None:
    if not _applies(inbound):
        return
    async with open_tenant_session(ctx["session_factory"], tenant_id) as session:
        await _save_media_row(
            session,
            tenant_id,
            uuid.UUID(message_id),
            status="skipped",
            error_code=error_code,
            only_if_not_done=True,
        )
        await session.commit()


async def _download(inbound: InboundContext) -> bytes:
    if inbound.whatsapp_provider == "zapi":
        token = (
            decrypt_access_token(inbound.zapi_client_token_encrypted)
            if inbound.zapi_client_token_encrypted
            else None
        )
        return await download_zapi_media(inbound.media_url, token, settings.attachment_max_bytes)
    return await download_meta_media(
        inbound.media_url,
        decrypt_access_token(inbound.access_token_encrypted),
        settings.attachment_max_bytes,
    )


async def prepare_media(
    ctx: dict, *, tenant_id: str, conversation_id: str, message_id: str, inbound: InboundContext
) -> str | None:
    if not _applies(inbound):
        return None
    session_factory = ctx["session_factory"]
    mid = uuid.UUID(message_id)

    async with open_tenant_session(session_factory, tenant_id) as session:
        row = await _load_media_row(session, mid)
        if row is not None and row.status == "done":
            return transcript_note(row.text or "", bool(row.uncertain_spans))
        if (
            row is not None
            and row.status == "failed"
            and (
                row.error_code not in TRANSIENT_ERRORS
                or row.attempts >= settings.media_max_attempts
            )
        ):
            return failure_note(row.error_code)

        payer = transcription_payer(inbound)
        if payer is None:
            await _save_media_row(
                session,
                tenant_id,
                mid,
                status="skipped",
                error_code="no_credits",
                only_if_not_done=True,
            )
            await session.commit()
            _log(tenant_id, conversation_id, message_id, "skipped", "no_credits", 0)
            return None

        attempts = (row.attempts if row is not None else 0) + 1
        await _save_media_row(
            session,
            tenant_id,
            mid,
            status="processing",
            error_code=None,
            attempts=attempts,
            started_at=datetime.now(UTC),
        )
        await session.commit()

    metrics: dict = {}
    duration: float | None = None
    try:
        t0 = time.monotonic()
        audio = await _download(inbound)
        metrics["download_ms"] = int((time.monotonic() - t0) * 1000)
        audio_format = detect_audio_format(audio)
        if audio_format is None:
            raise _MediaFailure("invalid_format")
        duration = await asyncio.to_thread(read_duration_seconds, audio)
        if duration is None:
            raise _MediaFailure("invalid_format")
        metrics["duration_s"] = round(duration, 2)
        if duration > settings.audio_max_duration_seconds:
            raise _MediaFailure("too_long")
        t1 = time.monotonic()
        result = await transcribe(audio, f"audio-{message_id}.{audio_format}")
        metrics["transcribe_ms"] = int((time.monotonic() - t1) * 1000)
        if not any(ch.isalnum() for ch in result.text):
            raise _MediaFailure("silence")
    except (MediaTooLargeError, MediaDownloadError, TranscriptionError, _MediaFailure) as exc:
        if isinstance(exc, MediaTooLargeError):
            error_code = "too_large"
        elif isinstance(exc, MediaDownloadError):
            error_code = "download_failed"
        else:
            error_code = exc.error_code
        async with open_tenant_session(session_factory, tenant_id) as session:
            await _save_media_row(
                session,
                tenant_id,
                mid,
                status="failed",
                error_code=error_code,
                attempts=attempts,
                finished_at=datetime.now(UTC),
                duration_seconds=Decimal(str(round(duration, 2))) if duration else None,
            )
            await session.commit()
        _log(tenant_id, conversation_id, message_id, "failed", error_code, attempts, **metrics)
        job_try = ctx.get("job_try", 1)
        if (
            error_code in TRANSIENT_ERRORS
            and attempts < settings.media_max_attempts
            and job_try < MAX_TRIES
        ):
            raise Retry(defer=job_try * 10) from None
        return failure_note(error_code)

    duration_dec = Decimal(str(round(duration, 2)))
    cost_usd = (duration_dec / 60 * Decimal(str(settings.transcription_usd_per_minute))).quantize(
        Decimal("0.000001")
    )
    async with open_tenant_session(session_factory, tenant_id) as session:
        current = await _load_media_row(session, mid)  # trava a linha: evita débito duplo
        if current is not None and current.status == "done":
            return transcript_note(current.text or "", bool(current.uncertain_spans))
        config = await get_current_pricing_config(session)
        if config.transcription_credits_per_minute is None:
            logger.warning(
                "pricing_config sem transcription_credits_per_minute; transcrição sem custo "
                "tenant_id=%s message_id=%s pricing_config_id=%s",
                tenant_id,
                message_id,
                config.id,
            )
        rate = config.transcription_credits_per_minute or Decimal(0)
        credits = (duration_dec / 60 * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        billed = Decimal(0)
        if credits and payer == "end_customer":
            billed = await _debitar_creditos_cliente_final(
                session,
                tenant_id,
                inbound.contact_phone_number,
                mid,
                0,
                credits,
                pricing_config_id=config.id,
                description=_DESCRIPTION,
            )
        elif credits and payer == "tenant":
            _, billed = await _debitar_creditos(
                session,
                tenant_id,
                mid,
                0,
                credits,
                pricing_config_id=config.id,
                description=_DESCRIPTION,
            )
        await _save_media_row(
            session,
            tenant_id,
            mid,
            status="done",
            error_code=None,
            attempts=attempts,
            text=result.text,
            uncertain_spans=result.uncertain_spans or None,
            duration_seconds=duration_dec,
            provider=f"openai:{settings.transcription_model}",
            cost_usd=cost_usd,
            credits_consumed=billed,
            finished_at=datetime.now(UTC),
        )
        await session.commit()
    metrics["cost_usd"] = cost_usd
    _log(tenant_id, conversation_id, message_id, "done", None, attempts, **metrics)
    return transcript_note(result.text, bool(result.uncertain_spans))
