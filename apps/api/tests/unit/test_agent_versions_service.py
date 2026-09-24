import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.agent_versions import test_snapshot as load_snapshot


@pytest.mark.parametrize("revision", [3, None])
async def test_snapshot_rejects_changed_or_deleted_agent(revision):
    session = AsyncMock()
    session.scalar.side_effect = [
        SimpleNamespace(agent_id=uuid.uuid4(), draft_revision=2),
        revision,
    ]
    with pytest.raises(HTTPException) as caught:
        await load_snapshot(session, uuid.uuid4(), uuid.uuid4())
    assert caught.value.status_code == 409


async def test_snapshot_is_tenant_scoped_and_keeps_saved_configuration():
    session = AsyncMock()
    tenant_id, conversation_id, agent_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    snapshot = [{"instructions": "Saved draft"}]
    session.scalar.side_effect = [
        SimpleNamespace(agent_id=agent_id, draft_revision=2, agents_snapshot=snapshot),
        2,
    ]
    assert (await load_snapshot(session, tenant_id, conversation_id)).agents_snapshot == snapshot
    first, second = [call.args[0].compile().params for call in session.scalar.await_args_list]
    assert tenant_id in first.values() and conversation_id in first.values()
    assert tenant_id in second.values() and agent_id in second.values()
