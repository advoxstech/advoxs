import io
import wave

import pytest

from app.audio_validation import (
    detect_audio_format,
    is_audio,
    normalize_mime,
    read_duration_seconds,
)
from app.config import Settings, validate_transcription_config


def _wav(seconds: float, rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("media_type", "expected"),
    [
        ("audio/ogg; codecs=opus", True),
        ("Audio/OGG", True),
        ("audio", True),  # Z-API sem mimeType cai no kind
        ("audio/mpeg", True),
        ("application/pdf", False),
        ("image/jpeg", False),
        (None, False),
        ("", False),
    ],
)
def test_is_audio(media_type, expected) -> None:
    assert is_audio(media_type) is expected


def test_normalize_mime_remove_parametros_e_caixa() -> None:
    assert normalize_mime(" Audio/OGG; codecs=opus ") == "audio/ogg"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"OggS\x00\x02" + b"\x00" * 20, "ogg"),
        (b"ID3\x04\x00" + b"\x00" * 20, "mp3"),
        (b"\xff\xfb\x90\x00" + b"\x00" * 20, "mp3"),
        (b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 20, "m4a"),
        (_wav(0.1), "wav"),
        (b"#!AMR\n" + b"\x00" * 20, None),
        (b"\x1a\x45\xdf\xa3" + b"\x00" * 20, None),  # WebM fora do escopo
        (b"%PDF-1.4", None),
        (b"", None),
    ],
)
def test_detect_audio_format(data, expected) -> None:
    assert detect_audio_format(data) == expected


def test_read_duration_de_wav_real() -> None:
    assert read_duration_seconds(_wav(2.0)) == pytest.approx(2.0, abs=0.05)


def test_read_duration_ilegivel_devolve_none() -> None:
    assert read_duration_seconds(b"OggS lixo sem cabecalho valido") is None


def test_transcricao_ligada_sem_chave_falha() -> None:
    s = Settings(
        app_database_url="x",
        system_database_url="x",
        redis_url="x",
        audio_transcription_enabled=True,
        openai_api_key="",
    )
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        validate_transcription_config(s)


def test_transcricao_desligada_nao_exige_chave() -> None:
    s = Settings(
        app_database_url="x",
        system_database_url="x",
        redis_url="x",
        audio_transcription_enabled=False,
        openai_api_key="",
    )
    validate_transcription_config(s)
