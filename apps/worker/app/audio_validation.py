"""Validação de áudio recebido do contato, antes de qualquer chamada paga.

Tipo real por magic bytes (o mime declarado pela Meta/Z-API não é confiável)
e duração via mutagen (Python puro — sem ffmpeg na imagem). WebM e AMR ficam
fora: o mutagen não lê duração de WebM e a OpenAI não aceita AMR."""

import io

from mutagen import File as MutagenFile


def normalize_mime(media_type: str | None) -> str:
    return (media_type or "").split(";", 1)[0].strip().lower()


def is_audio(media_type: str | None) -> bool:
    mime = normalize_mime(media_type)
    return mime == "audio" or mime.startswith("audio/")


def detect_audio_format(data: bytes) -> str | None:
    if data[:4] == b"OggS":
        return "ogg"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    if data[4:8] == b"ftyp":
        return "m4a"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    return None


def read_duration_seconds(data: bytes) -> float | None:
    try:
        parsed = MutagenFile(io.BytesIO(data))
    except Exception:
        return None
    length = getattr(getattr(parsed, "info", None), "length", None)
    if not length:
        return None
    return float(length)
