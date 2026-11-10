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
    links_result = SimpleNamespace(all=lambda: [(AGENT_ID, FILE_ID, None)])
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


async def test_copias_de_pacote_vao_tambem_em_reference_file_ids(session):
    package_file = uuid.uuid4()
    agent_row = SimpleNamespace(
        id=AGENT_ID, name="Contratos", instructions="instruções", is_entry_point=False
    )
    agents_result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [agent_row]))
    links_result = SimpleNamespace(
        all=lambda: [(AGENT_ID, FILE_ID, None), (AGENT_ID, package_file, uuid.uuid4())]
    )
    session.execute = AsyncMock(side_effect=[agents_result, links_result])

    result = await load_agents_for_engine(session, TENANT_ID)

    assert result[0]["knowledge_base_file_ids"] == [str(FILE_ID), str(package_file)]
    assert result[0]["reference_file_ids"] == [str(package_file)]


async def test_consulta_exclui_a_pasta_nao_selecionaveis(session):
    agents_result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
    links_result = SimpleNamespace(all=lambda: [])
    session.execute = AsyncMock(side_effect=[agents_result, links_result])

    await load_agents_for_engine(session, TENANT_ID)

    query = str(
        session.execute.await_args_list[1].args[0].compile(compile_kwargs={"literal_binds": True})
    )
    assert "category IS DISTINCT FROM 'nao_selecionaveis'" in query


async def test_sem_agentes_retorna_lista_vazia(session):
    agents_result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
    links_result = SimpleNamespace(all=lambda: [])
    session.execute = AsyncMock(side_effect=[agents_result, links_result])

    result = await load_agents_for_engine(session, TENANT_ID)

    assert result == []
