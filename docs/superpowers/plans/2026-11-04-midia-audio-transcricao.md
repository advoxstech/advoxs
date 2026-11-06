# Transcrição de áudio do contato — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Áudio do contato (Meta/Z-API) é transcrito no worker, gravado em `message_media`, entregue ao agente (ou ao checkpoint/painel em modo humano) com marcação de incerteza, cobrado uma única vez e nunca reprocessado em eventos repetidos.

**Architecture:** Etapa `prepare_media` (`apps/worker/app/tasks/media.py`) chamada dentro de `_process_inbound_message`, antes das saídas por estado. Estado e resultado ficam numa tabela nova `message_media` (1:1 com `messages`, RLS), que também é o contrato para o OCR do João. Transcrição via REST da OpenAI (`gpt-4o-transcribe`, `httpx`, sem SDK novo), validação por magic bytes + `mutagen`. API expõe `media` em `MessageOut`; o painel mostra a transcrição na bolha.

**Tech Stack:** Python 3.12, Arq, SQLAlchemy Core (worker) / ORM (api), PostgreSQL 16 + RLS, httpx, mutagen, Next.js 15 + Vitest.

**Spec:** `docs/superpowers/specs/2026-11-04-midia-audio-transcricao-design.md`

## Global Constraints

- Modelo: `gpt-4o-transcribe`, `language=pt`, `response_format=json`, `include[]=logprobs`, timeout 120 s.
- `AUDIO_TRANSCRIPTION_ENABLED` default `false`; desligado = comportamento atual inalterado.
- Ligado sem `OPENAI_API_KEY` → worker não sobe (`RuntimeError` no startup).
- Limites: duração 600 s (`AUDIO_MAX_DURATION_SECONDS`), tamanho 20 MB (`attachment_max_bytes` atual), 3 tentativas de mídia (`MEDIA_MAX_ATTEMPTS`), concorrência 2 por processo (`TRANSCRIPTION_MAX_CONCURRENCY`), limiar de incerteza 0.5.
- Formatos aceitos por magic bytes: OGG (`OggS`), MP3 (`ID3` ou frame sync), M4A/MP4 (`ftyp` no offset 4), WAV (`RIFF`…`WAVE`). Qualquer outro → `invalid_format`.
- `error_code` (lista fechada): `download_failed`, `timeout`, `provider_unavailable` (transitórios); `invalid_format`, `too_large`, `too_long`, `silence` (permanentes); `no_credits`, `billing_gate` (skipped).
- Créditos: `duration_seconds / 60 × transcription_credits_per_minute`, `ROUND_HALF_UP` para inteiro; valor inicial 1.0; lançamento próprio "Transcrição de áudio"; falhas não são cobradas.
- Logs nunca contêm texto transcrito, trechos incertos, URL de mídia nem tokens; exceções só via `safe_error`.
- Nenhuma tela do tenant menciona "tokens".
- Ruff line-length 100; commits em Conventional Commits, terminando com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Mime com parâmetros/caixa (`audio/ogg; codecs=opus`, `Audio/OGG`) ou só o kind da Z-API (`audio`) precisa ser tratado como áudio — teste em Task 2 (`is_audio`).
2. Job que morre depois de gravar `done` (antes de chamar o agents) e é refeito: nenhuma nova chamada à OpenAI nem novo débito — teste em Task 5 (`test_done_reaproveita_sem_baixar_nem_cobrar`).
3. Linha presa em `processing` (worker morto no meio) vira nova tentativa, sem travar o atendimento depois de 3 — teste em Task 5 (`test_processing_orfao_conta_como_nova_tentativa`).
4. Transcrição só com pontuação/espaços (`"..."`, `" "`) é `silence`, não uma mensagem vazia enviada ao agente — teste em Task 5.
5. Texto transcrito vazando em log numa falha (ex.: exceção com o corpo da resposta) — teste em Task 5 (`test_logs_nao_contem_texto`).

---

## File Structure

| Arquivo | Responsabilidade |
|---|---|
| `apps/api/alembic/versions/0049_message_media.{py,sql}` | Tabela `message_media` + RLS + coluna/linha em `pricing_configs` |
| `apps/api/app/models/message_media.py` | Model ORM (leitura pelo painel) |
| `apps/worker/app/tables.py` | Table Core `message_media` + coluna nova de pricing |
| `apps/worker/app/audio_validation.py` | `normalize_mime`, `is_audio`, `detect_audio_format`, `read_duration_seconds` |
| `apps/worker/app/clients/transcription.py` | Chamada à OpenAI, semáforo, `mark_uncertain`, `TranscriptionError` |
| `apps/worker/app/tasks/wallet.py` | Débitos de wallet (movidos de `messages.py`, com `description`) |
| `apps/worker/app/tasks/media.py` | `prepare_media`, `record_media_skipped`, notas, persistência, cobrança, log |
| `apps/worker/app/tasks/messages.py` | Encaixe da etapa no fluxo |
| `apps/worker/app/config.py`, `app/worker.py` | Settings novas + validação no startup |
| `apps/api/app/schemas/conversations.py`, `app/api/v1/conversations.py` | `MessageOut.media` |
| `apps/web/src/lib/types.ts`, `src/components/MediaTranscript.tsx`, `src/components/ConversationThread.tsx` | Transcrição na bolha |
| `docker-compose.yml`, `.env.example` | Limites do worker + variáveis |
| `docs/audio-transcricao-operacao.md` | Publicação, migration/backup, métricas, registro de testes |

---

### Task 1: Migration `0049_message_media` + models

**Files:**
- Create: `apps/api/alembic/versions/0049_message_media.py`
- Create: `apps/api/alembic/versions/0049_message_media.sql`
- Create: `apps/api/app/models/message_media.py`
- Modify: `apps/api/app/models/__init__.py`
- Modify: `apps/api/app/models/billing.py` (classe `PricingConfig`)
- Modify: `apps/worker/app/tables.py`
- Test: `apps/api/tests/integration/test_rls_isolation.py`

**Interfaces:**
- Produces: tabela `message_media` (colunas do spec §4); `tables.message_media` (worker); `MessageMedia` ORM (api); coluna `pricing_configs.transcription_credits_per_minute` em `tables.pricing_configs` e `PricingConfig`.

- [ ] **Step 1: Write the failing integration test** — append to `apps/api/tests/integration/test_rls_isolation.py`:

```python
class TestMessageMediaRLS:
    async def test_message_media_isolada_por_tenant(self, seeded_tenants) -> None:
        owner = create_async_engine(settings.database_url)
        message_id = uuid.uuid4()
        async with owner.begin() as conn:
            conversation_id = (
                await conn.execute(
                    text("SELECT id FROM conversations WHERE tenant_id = :a"), {"a": TENANT_A}
                )
            ).scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO messages (id, conversation_id, tenant_id, sender_type, content) "
                    "VALUES (:m, :c, :a, 'contact', '')"
                ),
                {"m": message_id, "c": conversation_id, "a": TENANT_A},
            )
            await conn.execute(
                text(
                    "INSERT INTO message_media (message_id, tenant_id, kind, status) "
                    "VALUES (:m, :a, 'audio', 'done')"
                ),
                {"m": message_id, "a": TENANT_A},
            )
        try:
            app_engine = create_async_engine(settings.app_database_url)
            async with app_engine.begin() as conn:
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT_B)}
                )
                visible_to_b = (
                    await conn.execute(text("SELECT count(*) FROM message_media"))
                ).scalar_one()
            async with app_engine.begin() as conn:
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(TENANT_A)}
                )
                visible_to_a = (
                    await conn.execute(
                        text("SELECT count(*) FROM message_media WHERE message_id = :m"),
                        {"m": message_id},
                    )
                ).scalar_one()
            await app_engine.dispose()
            assert visible_to_b == 0
            assert visible_to_a == 1
        finally:
            async with owner.begin() as conn:
                # message_media sai em cascata com a mensagem.
                await conn.execute(text("DELETE FROM messages WHERE id = :m"), {"m": message_id})
                remaining = (
                    await conn.execute(
                        text("SELECT count(*) FROM message_media WHERE message_id = :m"),
                        {"m": message_id},
                    )
                ).scalar_one()
            await owner.dispose()
        assert remaining == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && docker compose up -d postgres && uv run pytest tests/integration/test_rls_isolation.py::TestMessageMediaRLS -v`
Expected: FAIL with `relation "message_media" does not exist`.

- [ ] **Step 3: Write the migration** — `apps/api/alembic/versions/0049_message_media.py`:

```python
"""Media processing state (audio transcription, OCR) and transcription pricing.

Revision ID: 0049
Revises: 0048
"""

from pathlib import Path

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text().split("\n-- statement\n"):
        op.execute(statement)


def downgrade():
    # A linha de pricing_configs inserida no upgrade fica: lançamentos de
    # transcrição podem referenciá-la (credit_transactions.pricing_config_id).
    op.execute("ALTER TABLE pricing_configs DROP COLUMN transcription_credits_per_minute")
    op.execute("DROP TABLE message_media")
```

`apps/api/alembic/versions/0049_message_media.sql`:

```sql
CREATE TABLE message_media (
 message_id uuid PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
 tenant_id uuid NOT NULL REFERENCES tenants(id),
 kind varchar(16) NOT NULL CHECK (kind IN ('audio','document','image')),
 status varchar(16) NOT NULL CHECK (status IN ('pending','processing','done','failed','skipped')),
 attempts integer NOT NULL DEFAULT 0,
 error_code varchar(32) CHECK (error_code IN ('download_failed','timeout','provider_unavailable',
  'invalid_format','too_large','too_long','silence','no_credits','billing_gate')),
 text text,
 uncertain_spans jsonb,
 duration_seconds numeric(8,2),
 provider varchar(64),
 cost_usd numeric(10,6),
 credits_consumed numeric(12,4),
 started_at timestamptz,
 finished_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
-- statement
CREATE INDEX message_media_tenant_status ON message_media(tenant_id, status);
-- statement
ALTER TABLE message_media ENABLE ROW LEVEL SECURITY;
-- statement
CREATE POLICY tenant_isolation ON message_media
 USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
 WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
-- statement
DO $$ BEGIN
 IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'advoxs_app') THEN
  GRANT SELECT, INSERT, UPDATE, DELETE ON message_media TO advoxs_app, advoxs_system;
 END IF;
END $$;
-- statement
ALTER TABLE pricing_configs ADD COLUMN transcription_credits_per_minute numeric(8,4);
-- statement
INSERT INTO pricing_configs
 (id, tokens_per_credit, input_weight, output_weight, transcription_credits_per_minute, effective_at)
SELECT gen_random_uuid(), tokens_per_credit, input_weight, output_weight, 1.0, now()
FROM pricing_configs
WHERE effective_at <= now()
ORDER BY effective_at DESC
LIMIT 1;
```

- [ ] **Step 4: Add the models**

`apps/api/app/models/message_media.py`:

```python
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MessageMedia(Base):
    """Estado do processamento de mídia de uma mensagem (migration 0049).

    Escrita só pelo worker (transcrição de áudio; OCR na frente do PDF).
    O painel apenas lê `kind`/`status`/`error_code`/`text`."""

    __tablename__ = "message_media"

    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(String(32))
    text: Mapped[str | None] = mapped_column(Text)
    uncertain_spans: Mapped[list | None] = mapped_column(JSONB)
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    provider: Mapped[str | None] = mapped_column(String(64))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    credits_consumed: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
```

In `apps/api/app/models/__init__.py`, add `from app.models.message_media import MessageMedia` next to the `message` import and `"MessageMedia"` to `__all__` (keep alphabetical order used by the file).

In `apps/api/app/models/billing.py`, class `PricingConfig`, after `output_weight`:

```python
    transcription_credits_per_minute: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
```

In `apps/worker/app/tables.py`: add `Column("transcription_credits_per_minute", Numeric(8, 4)),` to `pricing_configs` (after `output_weight`), and after the `messages` table:

```python
message_media = Table(
    "message_media",
    metadata,
    Column("message_id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("kind", String),
    Column("status", String),
    Column("attempts", Integer),
    Column("error_code", String),
    Column("text", Text),
    Column("uncertain_spans", JSONB),
    Column("duration_seconds", Numeric(8, 2)),
    Column("provider", String),
    Column("cost_usd", Numeric(10, 6)),
    Column("credits_consumed", Numeric(12, 4)),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)
```

- [ ] **Step 5: Apply, run, round-trip**

Run: `cd apps/api && uv run alembic upgrade head && uv run pytest tests/integration/test_rls_isolation.py -v`
Expected: all PASS.
Run: `uv run alembic downgrade 0048 && uv run alembic upgrade head`
Expected: both succeed without error.
Run: `uv run pytest tests/unit -q && cd ../worker && uv run pytest tests/unit -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add apps/api/alembic/versions/0049_message_media.* apps/api/app/models apps/worker/app/tables.py apps/api/tests/integration/test_rls_isolation.py
git commit -m "feat: adiciona tabela message_media e preço de transcrição"
```

---

### Task 2: Settings + validação de áudio

**Files:**
- Modify: `apps/worker/pyproject.toml` (dependência `mutagen`)
- Modify: `apps/worker/app/config.py`
- Modify: `apps/worker/app/worker.py`
- Create: `apps/worker/app/audio_validation.py`
- Test: `apps/worker/tests/unit/test_audio_validation.py`

**Interfaces:**
- Produces:
  - `normalize_mime(media_type: str | None) -> str`
  - `is_audio(media_type: str | None) -> bool`
  - `detect_audio_format(data: bytes) -> str | None` → `"ogg" | "mp3" | "m4a" | "wav" | None`
  - `read_duration_seconds(data: bytes) -> float | None`
  - `validate_transcription_config(s: Settings) -> None` (em `app/config.py`)
  - Settings: `audio_transcription_enabled: bool = False`, `openai_api_key: str = ""`, `openai_base_url: str = "https://api.openai.com/v1"`, `transcription_model: str = "gpt-4o-transcribe"`, `transcription_timeout_seconds: float = 120`, `transcription_max_concurrency: int = 2`, `transcription_uncertain_threshold: float = 0.5`, `transcription_usd_per_minute: float = 0.006`, `audio_max_duration_seconds: int = 600`, `media_max_attempts: int = 3`

- [ ] **Step 1: Add the dependency**

Run: `cd apps/worker && uv add "mutagen>=1.47.0"`
Expected: `pyproject.toml` and `uv.lock` updated.

- [ ] **Step 2: Write the failing tests** — `apps/worker/tests/unit/test_audio_validation.py`:

```python
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
        app_database_url="x", system_database_url="x", redis_url="x",
        audio_transcription_enabled=True, openai_api_key="",
    )
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        validate_transcription_config(s)


def test_transcricao_desligada_nao_exige_chave() -> None:
    s = Settings(
        app_database_url="x", system_database_url="x", redis_url="x",
        audio_transcription_enabled=False, openai_api_key="",
    )
    validate_transcription_config(s)
```

- [ ] **Step 3: Run to verify failure**

Run: `cd apps/worker && uv run pytest tests/unit/test_audio_validation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.audio_validation'`.

- [ ] **Step 4: Implement**

`apps/worker/app/audio_validation.py`:

```python
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
```

In `apps/worker/app/config.py`, inside `Settings` after `attachment_max_bytes`:

```python
    # Transcrição de áudio do contato (ver app/tasks/media.py). Desligada,
    # o áudio segue o comportamento anterior (nota "formato não suportado").
    audio_transcription_enabled: bool = False
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    transcription_model: str = "gpt-4o-transcribe"
    transcription_timeout_seconds: float = 120
    transcription_max_concurrency: int = 2
    transcription_uncertain_threshold: float = 0.5
    transcription_usd_per_minute: float = 0.006
    audio_max_duration_seconds: int = 600
    media_max_attempts: int = 3
```

After the class, before `settings = Settings()`:

```python
def validate_transcription_config(s: "Settings") -> None:
    if s.audio_transcription_enabled and not s.openai_api_key.strip():
        raise RuntimeError("AUDIO_TRANSCRIPTION_ENABLED=true exige OPENAI_API_KEY")
```

In `apps/worker/app/worker.py`: change `from app.config import settings` to `from app.config import settings, validate_transcription_config` and make the first line of `startup`:

```python
    validate_transcription_config(settings)
```

- [ ] **Step 5: Run tests**

Run: `cd apps/worker && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/worker/pyproject.toml apps/worker/uv.lock apps/worker/app/audio_validation.py apps/worker/app/config.py apps/worker/app/worker.py apps/worker/tests/unit/test_audio_validation.py
git commit -m "feat(worker): valida tipo e duração do áudio e configura transcrição"
```

---

### Task 3: Cliente de transcrição (OpenAI) com marcação de incerteza

**Files:**
- Create: `apps/worker/app/clients/transcription.py`
- Test: `apps/worker/tests/unit/test_transcription_client.py`

**Interfaces:**
- Consumes: settings da Task 2.
- Produces:
  - `@dataclass TranscriptionResult(text: str, uncertain_spans: list[dict])`
  - `class TranscriptionError(Exception)` com atributo `error_code: str` (`timeout` | `provider_unavailable` | `invalid_format`)
  - `mark_uncertain(text: str, logprobs: list[dict], threshold: float) -> TranscriptionResult`
  - `async transcribe(audio: bytes, filename: str, *, transport: httpx.AsyncBaseTransport | None = None) -> TranscriptionResult`

- [ ] **Step 1: Write the failing tests** — `apps/worker/tests/unit/test_transcription_client.py`:

```python
import math

import httpx
import pytest

from app.clients import transcription as client
from app.clients.transcription import TranscriptionError, mark_uncertain, transcribe
from app.config import settings


def _lp(token: str, prob: float) -> dict:
    return {"token": token, "logprob": math.log(prob)}


def test_sem_logprobs_devolve_texto_sem_marcas() -> None:
    result = mark_uncertain(" Olá, tudo bem? ", [], 0.5)
    assert result.text == "Olá, tudo bem?"
    assert result.uncertain_spans == []


def test_marca_palavras_incertas_e_junta_adjacentes() -> None:
    logprobs = [
        _lp(" Meu", 0.99), _lp(" CPF", 0.98), _lp(" é", 0.99),
        _lp(" 123", 0.2), _lp("45", 0.3), _lp(" 678", 0.1),
        _lp(",", 0.99), _lp(" obrigado", 0.97),
    ]
    result = mark_uncertain("ignorado", logprobs, 0.5)
    assert result.text == "Meu CPF é [?12345 678?], obrigado"
    assert result.uncertain_spans == [{"text": "12345 678", "confidence": 0.1}]


def test_palavra_com_um_token_incerto_inteira_fica_incerta() -> None:
    result = mark_uncertain("x", [_lp(" Ro", 0.9), _lp("berto", 0.3)], 0.5)
    assert result.text == "[?Roberto?]"


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_transcribe_envia_modelo_idioma_e_logprobs(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.content
        seen["path"] = request.url.path
        return httpx.Response(200, json={"text": "Bom dia", "logprobs": [_lp(" Bom", 0.9), _lp(" dia", 0.9)]})

    result = await transcribe(b"OggS...", "audio-1.ogg", transport=_transport(handler))

    assert result.text == "Bom dia"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["path"].endswith("/audio/transcriptions")
    for expected in (b"gpt-4o-transcribe", b'name="language"', b"pt", b"logprobs"):
        assert expected in seen["body"]


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "invalid_format"), (429, "provider_unavailable"), (500, "provider_unavailable"),
     (401, "provider_unavailable")],
)
async def test_transcribe_mapeia_status(status, code) -> None:
    transport = _transport(lambda r: httpx.Response(status, json={"error": {"message": "x"}}))
    with pytest.raises(TranscriptionError) as exc:
        await transcribe(b"OggS", "a.ogg", transport=transport)
    assert exc.value.error_code == code


async def test_transcribe_timeout() -> None:
    def handler(request):
        raise httpx.ReadTimeout("lento", request=request)

    with pytest.raises(TranscriptionError) as exc:
        await transcribe(b"OggS", "a.ogg", transport=_transport(handler))
    assert exc.value.error_code == "timeout"


async def test_transcribe_conexao_recusada() -> None:
    def handler(request):
        raise httpx.ConnectError("off", request=request)

    with pytest.raises(TranscriptionError) as exc:
        await transcribe(b"OggS", "a.ogg", transport=_transport(handler))
    assert exc.value.error_code == "provider_unavailable"


async def test_semaforo_respeita_concorrencia(monkeypatch) -> None:
    monkeypatch.setattr(settings, "transcription_max_concurrency", 1)
    monkeypatch.setattr(client, "_semaphore_instance", None)
    assert client._semaphore()._value == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/worker && uv run pytest tests/unit/test_transcription_client.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `apps/worker/app/clients/transcription.py`:

```python
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
        core = joined.strip()
        lead = joined[: len(joined) - len(joined.lstrip())]
        out.append(f"{lead}[?{core}?]")
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
    body = response.json()
    return mark_uncertain(
        body.get("text") or "",
        body.get("logprobs") or [],
        settings.transcription_uncertain_threshold,
    )
```

- [ ] **Step 4: Run tests**

Run: `cd apps/worker && uv run pytest tests/unit/test_transcription_client.py -v && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/app/clients/transcription.py apps/worker/tests/unit/test_transcription_client.py
git commit -m "feat(worker): cliente de transcrição com marcação de trechos incertos"
```

---

### Task 4: Extrair débitos de wallet para `wallet.py`

Refactor puro (sem mudança de comportamento) para `media.py` poder debitar sem import circular com `messages.py`.

**Files:**
- Create: `apps/worker/app/tasks/wallet.py`
- Modify: `apps/worker/app/tasks/messages.py` (remover as duas funções, reimportar)
- Test: `apps/worker/tests/unit/test_debitar_creditos_cliente_final.py`

**Interfaces:**
- Produces (mesmas assinaturas de hoje + kwarg novo `description: str = "Consumo do agente"`):
  - `async _debitar_creditos(session, tenant_id, message_id, tokens_used, credits, tokens_input=0, tokens_output=0, pricing_config_id=None, description="Consumo do agente") -> tuple[bool, Decimal]`
  - `async _debitar_creditos_cliente_final(session, tenant_id, contact_phone_number, message_id, tokens_used, credits, tokens_input=0, tokens_output=0, pricing_config_id=None, description="Consumo do agente") -> Decimal`
- `app.tasks.messages` continua expondo os dois nomes (re-export), então os testes e monkeypatches existentes não mudam.

- [ ] **Step 1: Write the failing test** — append to `apps/worker/tests/unit/test_debitar_creditos_cliente_final.py`:

```python
async def test_descricao_customizada_vai_para_o_ledger() -> None:
    from app.tasks import wallet

    session = FakeSession()
    await wallet._debitar_creditos(
        session, TENANT_ID, MESSAGE_ID, 0, Decimal("2"), description="Transcrição de áudio"
    )
    assert any(p.get("description") == "Transcrição de áudio" for p in session.executed)

    session = FakeSession()
    await wallet._debitar_creditos_cliente_final(
        session, TENANT_ID, CONTACT, MESSAGE_ID, 0, Decimal("2"),
        description="Transcrição de áudio",
    )
    assert any(p.get("description") == "Transcrição de áudio" for p in session.executed)


def test_messages_reexporta_os_debitos() -> None:
    from app.tasks import wallet

    assert messages_task._debitar_creditos is wallet._debitar_creditos
    assert messages_task._debitar_creditos_cliente_final is wallet._debitar_creditos_cliente_final
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/worker && uv run pytest tests/unit/test_debitar_creditos_cliente_final.py -v`
Expected: FAIL with `ImportError: cannot import name 'wallet'`.

- [ ] **Step 3: Move the code**

Create `apps/worker/app/tasks/wallet.py` with the module docstring `"""Débitos de wallet (tenant e cliente final), compartilhados pelo turno do agente e pela transcrição de áudio."""`, the imports the two functions need (`uuid`, `datetime/UTC`, `Decimal`, `insert`, `select`, `update` from sqlalchemy, `AsyncSession`, `from app import tables`), and **cut** `_debitar_creditos` and `_debitar_creditos_cliente_final` from `apps/worker/app/tasks/messages.py` (currently at ~lines 1261–1360) into it unchanged, except:
- add parameter `description: str = "Consumo do agente",` as the last parameter of each;
- replace `description="Consumo do agente",` inside each `insert(...)` with `description=description,`.

In `apps/worker/app/tasks/messages.py` add to the imports:

```python
from app.tasks.wallet import _debitar_creditos, _debitar_creditos_cliente_final
```

Remove any import in `messages.py` that ruff now reports unused.

- [ ] **Step 4: Run tests**

Run: `cd apps/worker && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS (all existing tests included).

- [ ] **Step 5: Commit**

```bash
git add apps/worker/app/tasks/wallet.py apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_debitar_creditos_cliente_final.py
git commit -m "refactor(worker): move débitos de wallet para módulo próprio"
```

---

### Task 5: `prepare_media` — estado, idempotência, cobrança, notas, logs

**Files:**
- Create: `apps/worker/app/tasks/media.py`
- Modify: `apps/worker/app/pricing.py` (selecionar a coluna nova)
- Test: `apps/worker/tests/unit/test_media_task.py`

**Interfaces:**
- Consumes: `is_audio`, `detect_audio_format`, `read_duration_seconds` (Task 2); `transcribe`, `TranscriptionError`, `TranscriptionResult` (Task 3); `_debitar_creditos`, `_debitar_creditos_cliente_final` (Task 4); `download_meta_media`, `download_zapi_media`, `MediaDownloadError`, `MediaTooLargeError` (`app/clients/media.py`); `decrypt_access_token` (`app/crypto.py`); `get_current_pricing_config` (`app/pricing.py`); `InboundContext`.
- Produces:
  - `async prepare_media(ctx: dict, *, tenant_id: str, conversation_id: str, message_id: str, inbound: InboundContext) -> str | None` — nota para anexar à mensagem, ou `None` (sem áudio, recurso desligado, ou `skipped`). Pode levantar `arq.worker.Retry` em erro transitório com `attempts < media_max_attempts`.
  - `async record_media_skipped(ctx: dict, *, tenant_id: str, message_id: str, inbound: InboundContext, error_code: str) -> None`
  - `transcription_payer(inbound: InboundContext) -> str | None` → `"tenant" | "end_customer" | "none" | None`
  - `transcript_note(text: str, uncertain: bool) -> str`, `failure_note(error_code: str) -> str`

- [ ] **Step 1: Update pricing select** — in `apps/worker/app/pricing.py::get_current_pricing_config`, add `tables.pricing_configs.c.transcription_credits_per_minute,` to the `select(...)` after `output_weight`.

- [ ] **Step 2: Write the failing tests** — `apps/worker/tests/unit/test_media_task.py`:

```python
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
        "media-id-1", "token-claro", settings.attachment_max_bytes
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
        whatsapp_provider="zapi", media_url="https://zapi/x.ogg",
        zapi_client_token_encrypted="cifrado-zapi", access_token_encrypted=None,
    )
    await _run(inbound)
    patched["download_zapi"].assert_awaited_once_with(
        "https://zapi/x.ogg", "token-claro", settings.attachment_max_bytes
    )


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
        status="done", error_code=None, attempts=1, text=SECRET_TEXT,
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
        (lambda m: setattr(m["download_meta"], "side_effect", MediaTooLargeError("x")), "too_large"),
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
        (lambda m: setattr(m["download_meta"], "side_effect", MediaDownloadError("x")), "download_failed"),
        (lambda m: setattr(m["transcribe"], "side_effect", TranscriptionError("timeout")), "timeout"),
        (
            lambda m: setattr(m["transcribe"], "side_effect", TranscriptionError("provider_unavailable")),
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
        _ctx(), tenant_id=TENANT_ID, message_id=MESSAGE_ID,
        inbound=_inbound(media_type="application/pdf"), error_code="billing_gate",
    )
    patched["save_row"].assert_not_awaited()
    await record_media_skipped(
        _ctx(), tenant_id=TENANT_ID, message_id=MESSAGE_ID, inbound=_inbound(),
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
```

- [ ] **Step 3: Run to verify failure**

Run: `cd apps/worker && uv run pytest tests/unit/test_media_task.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.tasks.media'`.

- [ ] **Step 4: Implement** — `apps/worker/app/tasks/media.py`:

```python
"""Processamento de mídia recebida do contato antes do turno do agente.

Hoje: transcrição de áudio. Estado e resultado em `message_media` (migration
0049), que é também o contrato do OCR (kind=document|image). Idempotente por
message_id: `done` nunca é refeito nem cobrado de novo; `processing` órfão
(worker morto) conta como nova tentativa. Ver
docs/superpowers/specs/2026-11-04-midia-audio-transcricao-design.md."""

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
    session: AsyncSession, tenant_id: str, message_id: uuid.UUID, **values
) -> None:
    values["updated_at"] = datetime.now(UTC)
    statement = pg_insert(tables.message_media).values(
        message_id=message_id, tenant_id=uuid.UUID(tenant_id), kind="audio", **values
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[tables.message_media.c.message_id], set_=values
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
            session, tenant_id, uuid.UUID(message_id), status="skipped", error_code=error_code
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
        if row is not None and row.status == "failed" and (
            row.error_code not in TRANSIENT_ERRORS
            or row.attempts >= settings.media_max_attempts
        ):
            return failure_note(row.error_code)

        payer = transcription_payer(inbound)
        if payer is None:
            await _save_media_row(session, tenant_id, mid, status="skipped", error_code="no_credits")
            await session.commit()
            _log(tenant_id, conversation_id, message_id, "skipped", "no_credits", 0)
            return None

        attempts = (row.attempts if row is not None else 0) + 1
        await _save_media_row(
            session, tenant_id, mid, status="processing", error_code=None,
            attempts=attempts, started_at=datetime.now(UTC),
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
        duration = read_duration_seconds(audio)
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
                session, tenant_id, mid, status="failed", error_code=error_code,
                attempts=attempts, finished_at=datetime.now(UTC),
                duration_seconds=Decimal(str(round(duration, 2))) if duration else None,
            )
            await session.commit()
        _log(tenant_id, conversation_id, message_id, "failed", error_code, attempts, **metrics)
        if error_code in TRANSIENT_ERRORS and attempts < settings.media_max_attempts:
            raise Retry(defer=attempts * 10) from None
        return failure_note(error_code)

    duration_dec = Decimal(str(round(duration, 2)))
    cost_usd = (duration_dec / 60 * Decimal(str(settings.transcription_usd_per_minute))).quantize(
        Decimal("0.000001")
    )
    async with open_tenant_session(session_factory, tenant_id) as session:
        config = await get_current_pricing_config(session)
        rate = config.transcription_credits_per_minute or Decimal(0)
        credits = (duration_dec / 60 * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        billed = Decimal(0)
        if credits and payer == "end_customer":
            billed = await _debitar_creditos_cliente_final(
                session, tenant_id, inbound.contact_phone_number, mid, 0, credits,
                pricing_config_id=config.id, description=_DESCRIPTION,
            )
        elif credits and payer == "tenant":
            _, billed = await _debitar_creditos(
                session, tenant_id, mid, 0, credits,
                pricing_config_id=config.id, description=_DESCRIPTION,
            )
        await _save_media_row(
            session, tenant_id, mid, status="done", error_code=None, attempts=attempts,
            text=result.text, uncertain_spans=result.uncertain_spans or None,
            duration_seconds=duration_dec, provider=f"openai:{settings.transcription_model}",
            cost_usd=cost_usd, credits_consumed=billed, finished_at=datetime.now(UTC),
        )
        await session.commit()
    metrics["cost_usd"] = cost_usd
    _log(tenant_id, conversation_id, message_id, "done", None, attempts, **metrics)
    return transcript_note(result.text, bool(result.uncertain_spans))
```

Note for the implementer: `test_sucesso_…` expects `credits_consumed == Decimal(1)` because the mocked `_debitar_creditos` returns `(False, Decimal(1))`; `cost_usd` for 30 s is `0.5 × 0.006 = 0.003000`.

- [ ] **Step 5: Run tests**

Run: `cd apps/worker && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/worker/app/tasks/media.py apps/worker/app/pricing.py apps/worker/tests/unit/test_media_task.py
git commit -m "feat(worker): transcreve áudio com idempotência, cobrança e notas"
```

---

### Task 6: Encaixar `prepare_media` no fluxo do turno

**Files:**
- Modify: `apps/worker/app/tasks/messages.py` (`_process_inbound_message`, ~lines 529–670)
- Test: `apps/worker/tests/unit/test_process_inbound_message.py`

**Interfaces:**
- Consumes: `prepare_media`, `record_media_skipped` (Task 5); `is_audio` (Task 2).

- [ ] **Step 1: Write the failing tests** — in `apps/worker/tests/unit/test_process_inbound_message.py`, add to the `patched` fixture dict `"prepare_media": AsyncMock(return_value=None), "record_skipped": AsyncMock(),` and the matching `monkeypatch.setattr(messages_task, "prepare_media", mocks["prepare_media"])` / `monkeypatch.setattr(messages_task, "record_media_skipped", mocks["record_skipped"])`. Then append:

```python
NOTE = "[Áudio do cliente, transcrito automaticamente]: quero falar do contrato"


def _audio_inbound(**kwargs) -> InboundContext:
    inbound = _inbound(**kwargs)
    inbound.message_content = ""
    inbound.media_url = "media-1"
    inbound.media_type = "audio/ogg; codecs=opus"
    return inbound


async def test_audio_em_modo_agent_vai_transcrito_para_o_agente(patched, monkeypatch) -> None:
    monkeypatch.setattr(settings, "audio_transcription_enabled", True)
    patched["load"].return_value = _audio_inbound()
    patched["prepare_media"].return_value = NOTE

    await messages_task._process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    assert patched["send"].await_args.kwargs["message"] == NOTE
    patched["attachment"].assert_not_awaited()


async def test_audio_em_modo_human_sincroniza_transcricao(patched, monkeypatch) -> None:
    monkeypatch.setattr(settings, "audio_transcription_enabled", True)
    patched["load"].return_value = _audio_inbound(state="human")
    patched["prepare_media"].return_value = NOTE

    await messages_task._process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_not_awaited()
    assert patched["sync"].await_args.kwargs["content"] == NOTE


async def test_audio_de_contato_human_only_e_transcrito_sem_agente(patched, monkeypatch) -> None:
    monkeypatch.setattr(settings, "audio_transcription_enabled", True)
    inbound = _audio_inbound(state="human")
    inbound.human_only = True
    patched["load"].return_value = inbound

    await messages_task._process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["prepare_media"].assert_awaited_once()
    patched["send"].assert_not_awaited()


async def test_billing_gate_registra_skipped_e_nao_transcreve(patched, monkeypatch) -> None:
    monkeypatch.setattr(settings, "audio_transcription_enabled", True)
    patched["load"].return_value = _audio_inbound()
    monkeypatch.setattr(messages_task, "maybe_enter_gate", AsyncMock(return_value=True))
    monkeypatch.setattr(messages_task, "handle_billing_gate", AsyncMock())

    await messages_task._process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["record_skipped"].assert_awaited_once()
    assert patched["record_skipped"].await_args.kwargs["error_code"] == "billing_gate"
    patched["prepare_media"].assert_not_awaited()


async def test_retry_da_transcricao_propaga(patched, monkeypatch) -> None:
    monkeypatch.setattr(settings, "audio_transcription_enabled", True)
    patched["load"].return_value = _audio_inbound()
    patched["prepare_media"].side_effect = Retry(defer=10)

    with pytest.raises(Retry):
        await messages_task._process_inbound_message(
            _ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID
        )
    patched["send"].assert_not_awaited()


async def test_audio_com_recurso_desligado_mantem_caminho_antigo(patched, monkeypatch) -> None:
    monkeypatch.setattr(settings, "audio_transcription_enabled", False)
    patched["load"].return_value = _audio_inbound()

    await messages_task._process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["attachment"].assert_awaited_once()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/worker && uv run pytest tests/unit/test_process_inbound_message.py -v`
Expected: the 6 new tests FAIL (`prepare_media` not an attribute of `messages_task`).

- [ ] **Step 3: Implement** in `apps/worker/app/tasks/messages.py`:

Imports:

```python
from app.audio_validation import is_audio
from app.tasks.media import prepare_media, record_media_skipped
```

Replace the `human_only` early return:

```python
    if inbound.human_only:
        # Registered human-only contacts never enter billing or agent generation,
        # but the audio is still transcribed for the panel (spec §3).
        await prepare_media(
            ctx,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message_id=message_id,
            inbound=inbound,
        )
        return
```

Inside `if entrou_no_gate:`, as the first statement:

```python
        await record_media_skipped(
            ctx,
            tenant_id=tenant_id,
            message_id=message_id,
            inbound=inbound,
            error_code="billing_gate",
        )
```

Right after the gate block (before `if inbound.conversation_state != "agent":`):

```python
    media_note = await prepare_media(
        ctx,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        message_id=message_id,
        inbound=inbound,
    )
    message_content = inbound.message_content
    if media_note:
        message_content = f"{message_content}\n{media_note}".strip()
```

In the `conversation_state != "agent"` branch and in the saldo-esgotado branch, change the `_sync_context(..., inbound.message_content)` calls to `_sync_context(..., message_content)`.

Replace the attachment block (`attachment_note = await process_inbound_attachment(...)` through `message_content = f"{message_content}\n{attachment_note}".strip()`) with:

```python
    audio_handled = settings.audio_transcription_enabled and is_audio(inbound.media_type)
    attachment_note = None
    if not audio_handled:
        attachment_note = await process_inbound_attachment(
            rag_http,
            tenant_id=tenant_id,
            conversation_id=inbound.contact_phone_number,
            message_id=message_id,
            media_ref=inbound.media_url,
            media_type=inbound.media_type,
            whatsapp_provider=inbound.whatsapp_provider,
            access_token=meta_access_token,
            zapi_client_token=zapi_client_token,
        )
    if attachment_note:
        message_content = f"{message_content}\n{attachment_note}".strip()
```

(Keep the long existing comment about `conversation_id=contact_phone_number` above this block. Make sure `settings` is imported in `messages.py`; add `from app.config import settings` if missing.)

- [ ] **Step 4: Run tests**

Run: `cd apps/worker && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS, including every pre-existing test.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/app/tasks/messages.py apps/worker/tests/unit/test_process_inbound_message.py
git commit -m "feat(worker): usa a transcrição do áudio no turno e no modo humano"
```

---

### Task 7: API `MessageOut.media` + transcrição no painel

**Files:**
- Modify: `apps/api/app/schemas/conversations.py`
- Modify: `apps/api/app/api/v1/conversations.py` (`list_messages`)
- Test: `apps/api/tests/unit/test_conversations_routes.py`
- Modify: `apps/web/src/lib/types.ts`
- Create: `apps/web/src/components/MediaTranscript.tsx`
- Modify: `apps/web/src/components/ConversationThread.tsx` (~line 578)
- Test: `apps/web/__tests__/MediaTranscript.test.tsx`

**Interfaces:**
- Consumes: `MessageMedia` (Task 1).
- Produces: `MessageMediaOut {kind, status, error_code, text}`; `MessageOut.media: MessageMediaOut | None = None`; TS `MessageMedia` + `Message.media?: MessageMedia | null`; `<MediaTranscript media={...} />`.

- [ ] **Step 1: Write the failing API test** — in `apps/api/tests/unit/test_conversations_routes.py`, class containing `test_lista_mensagens`, add:

```python
    def test_lista_mensagens_inclui_transcricao_do_audio(self, client, session) -> None:
        session.scalar.return_value = _conversation()
        message_id = uuid.uuid4()
        message = SimpleNamespace(
            id=message_id,
            sender_type="contact",
            content="",
            media_url="media-1",
            media_type="audio/ogg",
            delivery_status=None,
            created_at=datetime.now(UTC),
        )
        media = SimpleNamespace(
            message_id=message_id, kind="audio", status="done", error_code=None,
            text="Quero revisar o contrato",
        )
        session.execute.side_effect = [_execute_returning([message]), _execute_returning([media])]

        response = client.get(f"/api/v1/conversations/{CONVERSATION_ID}/messages")

        assert response.status_code == 200
        assert response.json()[0]["media"] == {
            "kind": "audio", "status": "done", "error_code": None,
            "text": "Quero revisar o contrato",
        }
```

Run: `cd apps/api && uv run pytest tests/unit/test_conversations_routes.py -k transcricao -v`
Expected: FAIL (`KeyError: 'media'`).

- [ ] **Step 2: Implement the API**

In `apps/api/app/schemas/conversations.py`, before `MessageOut`:

```python
class MessageMediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: Literal["audio", "document", "image"]
    status: Literal["pending", "processing", "done", "failed", "skipped"]
    error_code: str | None = None
    text: str | None = None
```

and in `MessageOut`, after `media_type`: `media: MessageMediaOut | None = None`.

In `apps/api/app/api/v1/conversations.py::list_messages`, replace the final `return [...]` with:

```python
    messages = result.scalars().all()
    media_by_message: dict[uuid.UUID, MessageMedia] = {}
    with_media = [m.id for m in messages if m.media_url]
    if with_media:
        media_rows = await session.execute(
            select(MessageMedia).where(MessageMedia.message_id.in_(with_media))
        )
        media_by_message = {row.message_id: row for row in media_rows.scalars().all()}
    out = []
    for m in messages:
        item = MessageOut.model_validate(m)
        if m.id in media_by_message:
            item.media = MessageMediaOut.model_validate(media_by_message[m.id])
        out.append(item)
    return out
```

Add `MessageMedia` to the `app.models` import and `MessageMediaOut` to the `app.schemas.conversations` import in that file.

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 3: Write the failing web test** — `apps/web/__tests__/MediaTranscript.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MediaTranscript } from "@/components/MediaTranscript";

describe("MediaTranscript", () => {
  it("mostra a transcrição e destaca trechos incertos", () => {
    render(
      <MediaTranscript
        media={{ kind: "audio", status: "done", error_code: null, text: "CPF [?123?] ok" }}
      />,
    );
    expect(screen.getByText("Transcrição automática")).toBeInTheDocument();
    expect(screen.getByText("123")).toHaveAttribute("title", "Trecho incerto");
  });

  it("mostra o motivo quando o áudio não foi transcrito", () => {
    render(
      <MediaTranscript
        media={{ kind: "audio", status: "failed", error_code: "too_long", text: null }}
      />,
    );
    expect(screen.getByText(/Áudio não transcrito/)).toHaveTextContent("passa do limite de duração");
  });

  it("indica processamento em andamento", () => {
    render(
      <MediaTranscript
        media={{ kind: "audio", status: "processing", error_code: null, text: null }}
      />,
    );
    expect(screen.getByText("Transcrevendo áudio…")).toBeInTheDocument();
  });

  it("não renderiza nada para mídia que não é áudio", () => {
    const { container } = render(
      <MediaTranscript
        media={{ kind: "document", status: "done", error_code: null, text: "x" }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
```

Run: `cd apps/web && pnpm vitest run __tests__/MediaTranscript.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 4: Implement the web side**

In `apps/web/src/lib/types.ts`, above `Message`:

```ts
export interface MessageMedia {
  kind: "audio" | "document" | "image";
  status: "pending" | "processing" | "done" | "failed" | "skipped";
  error_code: string | null;
  text: string | null;
}
```

and add `media?: MessageMedia | null;` to `Message` after `media_type`.

`apps/web/src/components/MediaTranscript.tsx`:

```tsx
import type { MessageMedia } from "@/lib/types";

const REASONS: Record<string, string> = {
  download_failed: "não foi possível baixar o arquivo",
  timeout: "o serviço demorou demais",
  provider_unavailable: "serviço de transcrição indisponível",
  invalid_format: "formato não suportado",
  too_large: "arquivo maior que 20 MB",
  too_long: "passa do limite de duração",
  silence: "sem fala detectada",
  no_credits: "sem créditos",
  billing_gate: "aguardando pagamento do cliente",
};

function highlight(text: string) {
  return text.split(/(\[\?.*?\?\])/g).map((part, i) =>
    part.startsWith("[?") && part.endsWith("?]") ? (
      <mark key={i} title="Trecho incerto" className="rounded-sm bg-brass-soft px-0.5">
        {part.slice(2, -2)}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

export function MediaTranscript({ media }: { media: MessageMedia }) {
  if (media.kind !== "audio") return null;
  if (media.status === "done" && media.text) {
    return (
      <div className="mt-2 border-t border-line pt-2">
        <span className="block font-mono text-micro uppercase tracking-[0.14em] text-muted">
          Transcrição automática
        </span>
        <p className="whitespace-pre-wrap break-words">{highlight(media.text)}</p>
      </div>
    );
  }
  if (media.status === "pending" || media.status === "processing") {
    return <p className="mt-2 text-xs text-muted">Transcrevendo áudio…</p>;
  }
  const reason = REASONS[media.error_code ?? ""] ?? "erro no processamento";
  return <p className="mt-2 text-xs text-muted">Áudio não transcrito: {reason}</p>;
}
```

In `apps/web/src/components/ConversationThread.tsx`, add `import { MediaTranscript } from "./MediaTranscript";` and change the contact-attachment branch to:

```tsx
        {message.media_url && fromContact ? (
          <>
            <ContactAttachment
              conversationId={conversationId}
              message={message}
            />
            {message.media ? <MediaTranscript media={message.media} /> : null}
          </>
        ) : message.media_url ? (
```

- [ ] **Step 5: Run web checks**

Run: `cd apps/web && pnpm vitest run && pnpm lint`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/schemas/conversations.py apps/api/app/api/v1/conversations.py apps/api/tests/unit/test_conversations_routes.py apps/web/src/lib/types.ts apps/web/src/components/MediaTranscript.tsx apps/web/src/components/ConversationThread.tsx apps/web/__tests__/MediaTranscript.test.tsx
git commit -m "feat: mostra a transcrição do áudio do contato no painel"
```

---

### Task 8: Operação — limites, variáveis, documentação e registro de testes

**Files:**
- Modify: `docker-compose.yml` (serviço `worker`)
- Modify: `.env.example`
- Create: `docs/audio-transcricao-operacao.md`
- Modify: `CLAUDE.md` (Fluxo de mensagem entrante, Modelo de Dados)

- [ ] **Step 1: Limits** — in `docker-compose.yml`, service `worker`, after `logging: *default-logging`:

```yaml
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 768M
```

Run: `docker compose config --quiet && echo ok`
Expected: `ok`.

- [ ] **Step 2: Env** — append to `.env.example`:

```bash
# Transcrição de áudio do contato (worker). Desligada por padrão.
AUDIO_TRANSCRIPTION_ENABLED=false
OPENAI_API_KEY=
# Opcionais (valores padrão):
# TRANSCRIPTION_MODEL=gpt-4o-transcribe
# AUDIO_MAX_DURATION_SECONDS=600
# TRANSCRIPTION_MAX_CONCURRENCY=2
# TRANSCRIPTION_TIMEOUT_SECONDS=120
# TRANSCRIPTION_UNCERTAIN_THRESHOLD=0.5
# TRANSCRIPTION_USD_PER_MINUTE=0.006
# MEDIA_MAX_ATTEMPTS=3
```

- [ ] **Step 3: Write `docs/audio-transcricao-operacao.md`** with these sections, filled with the concrete content below:

```markdown
# Transcrição de áudio — operação

## Diagrama
WhatsApp (Meta/Z-API) → api (webhook, persiste `messages`, `inbound_message_jobs`)
→ Arq → worker `_process_inbound_message` → `prepare_media`
(download → validação → OpenAI → `message_media` + débito) → agents | checkpoint | painel.

## Publicação
1. Backup: `docker compose exec postgres pg_dump -U advoxs -Fc advoxs > backup-pre-0049.dump`.
2. Deploy normal (`deploy.yml` roda `alembic upgrade head`, aplica a 0049).
3. Conferir: `SELECT count(*) FROM message_media;` (0) e
   `SELECT transcription_credits_per_minute FROM pricing_configs ORDER BY effective_at DESC LIMIT 1;` (1.0000).
4. Definir `OPENAI_API_KEY` e `AUDIO_TRANSCRIPTION_ENABLED=true` no `.env`; `docker compose up -d worker`.
5. Enviar um áudio de teste e conferir a linha `Mídia processada | ... status=done` no log do worker.

## Reversão
- Sem perda de dados: `AUDIO_TRANSCRIPTION_ENABLED=false` + `docker compose up -d worker`
  (o áudio volta ao comportamento anterior).
- Schema: `uv run alembic downgrade 0048` (apaga `message_media`; a linha de pricing fica).
- Restauração completa: `pg_restore -U advoxs -d advoxs --clean backup-pre-0049.dump`.

## Teste de migration com backup (registrar resultado)
dump → `upgrade head` → `downgrade 0048` → `upgrade head` → `pg_restore` num banco descartável.
| Data | Passo | Resultado |
|---|---|---|

## Limites
| Item | Valor | Variável |
|---|---|---|
| Duração | 600 s | AUDIO_MAX_DURATION_SECONDS |
| Tamanho | 20 MB | (attachment_max_bytes) |
| Tentativas | 3 | MEDIA_MAX_ATTEMPTS |
| Concorrência por processo | 2 | TRANSCRIPTION_MAX_CONCURRENCY |
| Worker (compose) | 1 CPU / 768 MB | docker-compose.yml |

## Métricas (SQL)
-- Tempo médio e taxa de falha, últimos 7 dias
SELECT status, error_code, count(*),
       avg(extract(epoch FROM finished_at - started_at)) AS avg_seconds,
       sum(duration_seconds) / 60 AS audio_minutes, sum(cost_usd) AS cost_usd
FROM message_media
WHERE kind = 'audio' AND created_at > now() - interval '7 days'
GROUP BY status, error_code ORDER BY count(*) DESC;
-- Presos em processamento
SELECT message_id, tenant_id, attempts, started_at FROM message_media
WHERE status = 'processing' AND started_at < now() - interval '15 minutes';

## Recuperação
- Worker reiniciado no meio: `recover_inbound_message_jobs` (lease 15 min) refaz o job;
  `processing` órfão vira nova tentativa; `done` nunca é refeito nem recobrado.
- OpenAI fora do ar: 3 tentativas (10 s, 20 s), depois o agente pede para o cliente reenviar/escrever.

## Privacidade
Logs só com IDs, status, error_code, tentativas, duração, tempos e custo. Áudio só em memória.
Transcrição apagada em cascata com a conversa.

## Registro de testes (Etapa 4)
| # | Caso | Arquivo | Esperado | Resultado | Transcrição preserva fatos? | Respostas geradas |
|---|---|---|---|---|---|---|
| 1 | Áudio curto | | done, 1 resposta | | | |
| 2 | Próximo ao limite (~9 min 50 s) | | done | | | |
| 3 | Ruído | | done ou trechos [? ?] | | | |
| 4 | Voz baixa | | done ou trechos [? ?] | | | |
| 5 | Silêncio | | failed/silence, agente pede reenvio | | | |
| 6 | Números e valores | | valores corretos ou [? ?] | | | |
| 7 | Nomes próprios | | nomes corretos ou [? ?] | | | |
| 8 | Formato aceito (mp3/m4a) | | done | | | |
| 9 | Formato inválido (AMR/WebM) | | failed/invalid_format | | | |
| 10 | Sequência áudio + textos | | ordem preservada, 1 resposta | | | |
| 11 | Evento repetido (webhook) | | 1 transcrição, 1 débito | | | |
| 12 | Retry do job após done | | sem nova chamada/débito | | | |
| 13 | Acesso entre tenants | | RLS bloqueia | | | |

## Consumo da VPS (carga pequena)
| Cenário | CPU worker | Memória worker | Tempo médio | Observações |
|---|---|---|---|---|
```

- [ ] **Step 4: Update `CLAUDE.md`** — in "Fluxo de mensagem entrante", after the bullet "✅ **Anexo do contato (PDF/DOCX/TXT)…**", add:

```markdown
   - ✅ **Áudio do contato transcrito** (migration `0049`, `app/tasks/media.py`, spec `docs/superpowers/specs/2026-11-04-midia-audio-transcricao-design.md`): com `AUDIO_TRANSCRIPTION_ENABLED=true`, o `worker` baixa, valida (magic bytes, até 10 min/20 MB) e transcreve pela OpenAI (`gpt-4o-transcribe`) antes do turno, também em modo `human`/`human_only` (não no billing gate nem sem saldo). Estado em `message_media` (idempotente por `message_id`, 3 tentativas em erro transitório), trechos de baixa confiança marcados `[? ?]` para o agente confirmar, débito próprio "Transcrição de áudio" (1 crédito/min, `pricing_configs.transcription_credits_per_minute`). Painel mostra a transcrição na bolha. Operação: `docs/audio-transcricao-operacao.md`.
```

and in "Modelo de Dados", after `### messages`, add a `### message_media (tenant-scoped)` section listing the columns from spec §4 in the same bullet style.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.example docs/audio-transcricao-operacao.md CLAUDE.md
git commit -m "docs: operação da transcrição de áudio e limites do worker"
```
