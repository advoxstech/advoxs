"""Transcrição de áudio pela API da OpenAI (gpt-4o-transcribe).

REST direto via httpx (sem SDK novo). `include[]=logprobs` devolve a
probabilidade de cada token: palavras abaixo do limiar viram `[?…?]` no texto,
para o agente pedir confirmação em vez de completar fatos. Nunca loga o
conteúdo; erros saem como TranscriptionError com um error_code fechado."""

import asyncio
import math
from dataclasses import dataclass, field

import httpx

from app.config import settings


@dataclass
class TranscriptionResult:
    text: str
    uncertain_spans: list[dict] = field(default_factory=list)


class TranscriptionError(Exception):
    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


_semaphore_instance: asyncio.Semaphore | None = None


def _semaphore() -> asyncio.Semaphore:
    global _semaphore_instance
    if _semaphore_instance is None:
        _semaphore_instance = asyncio.Semaphore(settings.transcription_max_concurrency)
    return _semaphore_instance


def mark_uncertain(text: str, logprobs: list[dict], threshold: float) -> TranscriptionResult:
    if not logprobs:
        return TranscriptionResult(text=text.strip())

    if "".join(entry.get("token", "") for entry in logprobs).strip() != text.strip():
        # tokenização/bytes divergentes do texto final: não arrisca texto truncado
        return TranscriptionResult(text=text.strip())

    words: list[list] = []  # [texto_bruto, menor_probabilidade]
    for entry in logprobs:
        token = entry.get("token", "")
        prob = math.exp(entry.get("logprob", 0.0))
        if not words or token[:1].isspace():
            words.append([token, prob])
        else:
            words[-1][0] += token
            words[-1][1] = min(words[-1][1], prob)

    out: list[str] = []
    spans: list[dict] = []
    run: list[str] = []
    run_probs: list[float] = []

    def flush() -> None:
        if not run:
            return
        joined = "".join(run)
        start = next(i for i, ch in enumerate(joined) if ch.isalnum())
        end = max(i for i, ch in enumerate(joined) if ch.isalnum()) + 1
        core = joined[start:end]
        out.append(f"{joined[:start]}[?{core}?]{joined[end:]}")
        spans.append({"text": core, "confidence": round(min(run_probs), 2)})
        run.clear()
        run_probs.clear()

    for raw, prob in words:
        if prob < threshold and any(ch.isalnum() for ch in raw):
            run.append(raw)
            run_probs.append(prob)
        else:
            flush()
            out.append(raw)
    flush()
    return TranscriptionResult(text="".join(out).strip(), uncertain_spans=spans)


async def transcribe(
    audio: bytes, filename: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> TranscriptionResult:
    data = {
        "model": settings.transcription_model,
        "language": "pt",
        "response_format": "json",
        "include[]": "logprobs",
    }
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    try:
        async with _semaphore():
            async with httpx.AsyncClient(
                base_url=settings.openai_base_url,
                timeout=settings.transcription_timeout_seconds,
                transport=transport,
            ) as http:
                response = await http.post(
                    "/audio/transcriptions",
                    data=data,
                    files={"file": (filename, audio)},
                    headers=headers,
                )
    except httpx.TimeoutException as exc:
        raise TranscriptionError("timeout") from exc
    except httpx.HTTPError as exc:
        raise TranscriptionError("provider_unavailable") from exc

    if response.status_code == 400:
        raise TranscriptionError("invalid_format")
    if response.status_code >= 400:
        raise TranscriptionError("provider_unavailable")
    try:
        body = response.json()
    except ValueError as exc:
        raise TranscriptionError("provider_unavailable") from exc
    return mark_uncertain(
        body.get("text") or "",
        body.get("logprobs") or [],
        settings.transcription_uncertain_threshold,
    )
