import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from arq.worker import Retry
from cryptography.fernet import Fernet

from app.config import settings
from app.crypto import decrypt_access_token
from app.tasks import messages as messages_task
from app.tasks.messages import InboundContext, process_inbound_message

TENANT_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())
MESSAGE_ID = str(uuid.uuid4())


def _ctx() -> dict:
    session = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return {"session_factory": factory, "http": AsyncMock(), "job_try": 1}


def _inbound(state: str = "agent") -> InboundContext:
    return InboundContext(
        conversation_state=state,
        contact_phone_number="5511888888888",
        message_content="Olá",
        phone_number_id="PNID",
        access_token_encrypted="token-cifrado",
    )


@pytest.fixture
def patched(monkeypatch):
    mocks = {
        "load": AsyncMock(return_value=_inbound()),
        "decrypt": MagicMock(return_value="token-claro"),
        "send": AsyncMock(return_value=["resposta 1", "resposta 2"]),
        "persist": AsyncMock(),
    }
    monkeypatch.setattr(messages_task, "_load_context", mocks["load"])
    monkeypatch.setattr(messages_task, "decrypt_access_token", mocks["decrypt"])
    monkeypatch.setattr(messages_task, "send_message_to_agents", mocks["send"])
    monkeypatch.setattr(messages_task, "_persist_agent_responses", mocks["persist"])
    return mocks


async def test_agent_flow_persists_responses(patched) -> None:
    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["decrypt"].assert_called_once_with("token-cifrado")
    patched["send"].assert_awaited_once()
    assert patched["send"].await_args.kwargs["access_token"] == "token-claro"
    assert patched["send"].await_args.kwargs["message"] == "Olá"
    patched["persist"].assert_awaited_once()
    assert patched["persist"].await_args.args[3] == ["resposta 1", "resposta 2"]


async def test_human_state_skips_agent(patched) -> None:
    patched["load"].return_value = _inbound(state="human")

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_not_awaited()
    patched["persist"].assert_not_awaited()


async def test_missing_context_returns_early(patched) -> None:
    patched["load"].return_value = None

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["send"].assert_not_awaited()


async def test_debounced_202_does_not_persist(patched) -> None:
    patched["send"].return_value = None

    await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["persist"].assert_not_awaited()


async def test_http_error_raises_retry(patched) -> None:
    patched["send"].side_effect = httpx.ConnectError("agents fora do ar")

    with pytest.raises(Retry):
        await process_inbound_message(_ctx(), TENANT_ID, CONVERSATION_ID, MESSAGE_ID)

    patched["persist"].assert_not_awaited()


def test_decrypt_access_token_roundtrip(monkeypatch) -> None:
    key = Fernet.generate_key()
    monkeypatch.setattr(settings, "whatsapp_token_encryption_key", key.decode())
    encrypted = Fernet(key).encrypt(b"meu-token").decode()

    assert decrypt_access_token(encrypted) == "meu-token"


def test_decrypt_without_key_raises(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_token_encryption_key", "")

    with pytest.raises(RuntimeError):
        decrypt_access_token("qualquer")
