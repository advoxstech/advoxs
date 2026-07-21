import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.agents_engine import load_agents_for_engine

TENANT_ID = uuid.uuid4()
AGENT_ID = uuid.uuid4()
OTHER_AGENT_ID = uuid.uuid4()
FILE_ID = uuid.uuid4()


@pytest.fixture
def session():
    return AsyncMock()


async def test_monta_lista_com_arquivos_anexados(session):
    agent_row = SimpleNamespace(
        id=AGENT_ID, name="Secretária", instructions="instruções", is_entry_point=True
    )
    other_row = SimpleNamespace(
        id=OTHER_AGENT_ID,
        name="Condominial",
        instructions="outras instruções",
        is_entry_point=False,
    )
    agents_result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [agent_row, other_row])
    )
    links_result = SimpleNamespace(all=lambda: [(AGENT_ID, FILE_ID)])
    session.execute = AsyncMock(side_effect=[agents_result, links_result])

    result = await load_agents_for_engine(session, TENANT_ID)

    assert result == [
        {
            "id": str(AGENT_ID),
            "name": "Secretária",
            "instructions": "instruções",
            "is_entry_point": True,
            "knowledge_base_file_ids": [str(FILE_ID)],
        },
        {
            "id": str(OTHER_AGENT_ID),
            "name": "Condominial",
            "instructions": "outras instruções",
            "is_entry_point": False,
            "knowledge_base_file_ids": [],
        },
    ]


async def test_sem_agentes_retorna_lista_vazia(session):
    agents_result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
    links_result = SimpleNamespace(all=lambda: [])
    session.execute = AsyncMock(side_effect=[agents_result, links_result])

    result = await load_agents_for_engine(session, TENANT_ID)

    assert result == []
