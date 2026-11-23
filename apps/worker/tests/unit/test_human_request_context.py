from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.tasks.messages import _conversation_allows_automation


async def test_pending_request_is_refreshed_before_agent_generation():
    agent = uuid4()
    row = SimpleNamespace(
        state="agent",
        deletion_requested_at=None,
        human_requested_at="now",
        human_request_agent_id=agent,
    )
    result = MagicMock()
    result.one_or_none.return_value = row
    session = AsyncMock()
    session.execute.return_value = result
    inbound = SimpleNamespace(human_request=None)
    assert await _conversation_allows_automation(session, str(uuid4()), inbound=inbound)
    assert inbound.human_request == {"pending": True, "agent_id": str(agent)}


async def test_assumed_conversation_clears_stale_request_and_disallows_ai():
    row = SimpleNamespace(
        state="human",
        deletion_requested_at=None,
        human_requested_at=None,
        human_request_agent_id=None,
    )
    result = MagicMock()
    result.one_or_none.return_value = row
    session = AsyncMock()
    session.execute.return_value = result
    inbound = SimpleNamespace(human_request={"pending": True})
    assert not await _conversation_allows_automation(session, str(uuid4()), inbound=inbound)
    assert inbound.human_request is None
