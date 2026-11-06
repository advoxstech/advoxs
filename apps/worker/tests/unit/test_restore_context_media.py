import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.tasks import messages as messages_task
from app.tasks.media import failure_note, transcript_note


def _row(sender, content, status=None, text=None, spans=None, code=None, attempts=0):
    return SimpleNamespace(
        sender_type=sender,
        content=content,
        media_status=status,
        media_text=text,
        media_uncertain_spans=spans,
        media_error_code=code,
        media_attempts=attempts,
    )


async def _run(monkeypatch, rows):
    marker = MagicMock()
    marker.scalar_one_or_none.return_value = datetime.now(UTC)
    result = MagicMock()
    result.all.return_value = rows
    session = AsyncMock()
    session.execute.side_effect = [marker, result]
    replace = AsyncMock()
    monkeypatch.setattr(messages_task, "replace_context_to_agents", replace)
    await messages_task._restore_agent_context_before_stale_response(
        session, AsyncMock(), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), "5511"
    )
    return replace.await_args.kwargs["messages"]


@pytest.mark.asyncio
async def test_reconstroi_contexto_com_transcricao(monkeypatch) -> None:
    history = await _run(
        monkeypatch,
        [
            _row("contact", "", "done", "quero um contrato", [{"text": "x"}]),
            _row("agent", "ok"),
            _row("contact", "legenda", "done", "oi", None),
        ],
    )
    assert history[0] == {
        "role": "contact",
        "content": transcript_note("quero um contrato", True),
    }
    assert history[1] == {"role": "attendant", "content": "ok"}
    assert history[2]["content"] == f"legenda\n{transcript_note('oi', False)}"


@pytest.mark.asyncio
async def test_reconstroi_contexto_com_falha_terminal(monkeypatch) -> None:
    history = await _run(
        monkeypatch,
        [
            _row("contact", "", "failed", code="silence"),
            _row("contact", "", "failed", code="timeout", attempts=1),
            _row("contact", "", "failed", code="timeout", attempts=settings.media_max_attempts),
            _row("contact", "texto", "pending"),
            _row("contact", "puro"),
        ],
    )
    assert history[0]["content"] == failure_note("silence")
    assert history[1]["content"] == ""
    assert history[2]["content"] == failure_note("timeout")
    assert history[3]["content"] == "texto"
    assert history[4]["content"] == "puro"
