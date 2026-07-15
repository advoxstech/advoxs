from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage

import services.update_context as update_context_module
from services.update_context import add_context_messages


def _mock_checkpointer(monkeypatch):
    checkpointer = MagicMock()
    checkpointer.setup = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=checkpointer)
    cm.__aexit__ = AsyncMock(return_value=False)
    saver_cls = MagicMock()
    saver_cls.from_conn_string = MagicMock(return_value=cm)
    monkeypatch.setattr(update_context_module, "AsyncPostgresSaver", saver_cls)

    agent = MagicMock()
    agent.aupdate_state = AsyncMock()
    graph = MagicMock()
    graph.compile = MagicMock(return_value=agent)
    monkeypatch.setattr(update_context_module, "graph", graph)
    return agent


async def test_mapeia_roles_e_anexa_ao_checkpoint(monkeypatch):
    agent = _mock_checkpointer(monkeypatch)

    added = await add_context_messages(
        "tenant-1:5511999999999",
        [
            {"role": "contact", "content": "oi, ainda tá aí?"},
            {"role": "attendant", "content": "sim! sou o Dr. Silva, vou te ajudar"},
        ],
        db_uri="postgresql://x",
    )

    assert added == 2
    agent.aupdate_state.assert_awaited_once()
    config, values = agent.aupdate_state.await_args.args
    assert config == {"configurable": {"thread_id": "tenant-1:5511999999999"}}
    lc_messages = values["messages"]
    assert isinstance(lc_messages[0], HumanMessage)
    assert lc_messages[0].content == "oi, ainda tá aí?"
    assert isinstance(lc_messages[1], AIMessage)
    assert lc_messages[1].content == "sim! sou o Dr. Silva, vou te ajudar"
