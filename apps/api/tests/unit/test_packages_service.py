import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.clients.rag import RagApiError
from app.models import AgentKnowledgeBaseFile, KnowledgeBaseFile
from app.services import packages

TENANT_ID = uuid.uuid4()
AGENT_ID = uuid.uuid4()


def _agent(specialty: str | None = "contratos") -> SimpleNamespace:
    return SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID, specialty=specialty)


def _document(sha: str = "a" * 64) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        code="1.01",
        filename="1.01_Codigo_Civil_texto_oficial.pdf",
        category="legislacao_especifica",
        mime_type="application/pdf",
        size_bytes=3_000_000,
        content_sha256=sha,
        pages_total=194,
        partial_pages=None,
    )


def _rows(items):
    return SimpleNamespace(all=lambda: items)


@pytest.fixture
def session():
    mock = AsyncMock()
    mock.add = MagicMock()
    mock.scalar.return_value = None  # nenhum arquivo próprio com o mesmo nome
    return mock


def _added(session, kind):
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], kind)]


async def test_sem_especialidade_nao_faz_nada(session) -> None:
    assert await packages.sync_agent_package(session, _agent(specialty=None)) == []
    session.scalars.assert_not_awaited()


async def test_pacote_vazio_nao_cria_nada(session) -> None:
    session.scalars.side_effect = [_rows([])]

    assert await packages.sync_agent_package(session, _agent()) == []
    session.add.assert_not_called()


async def test_cria_a_copia_marcada_como_pacote_e_liga_ao_agente(session) -> None:
    document = _document()
    session.scalars.side_effect = [_rows([document]), _rows([]), _rows([])]

    to_copy = await packages.sync_agent_package(session, _agent())

    (record,) = _added(session, KnowledgeBaseFile)
    assert to_copy == [record.id]
    assert record.tenant_id == TENANT_ID
    assert record.public_document_id == document.id
    assert record.filename == document.filename
    assert record.category == "legislacao_especifica"
    assert record.status == "processing"
    assert record.content_sha256 == document.content_sha256
    assert record.pages_total == 194
    (link,) = _added(session, AgentKnowledgeBaseFile)
    assert (link.agent_id, link.knowledge_base_file_id) == (AGENT_ID, record.id)


async def test_nome_ja_usado_por_arquivo_do_escritorio_ganha_sufixo(session) -> None:
    session.scalars.side_effect = [_rows([_document()]), _rows([]), _rows([])]
    session.scalar.return_value = uuid.uuid4()

    await packages.sync_agent_package(session, _agent())

    (record,) = _added(session, KnowledgeBaseFile)
    assert record.filename == "1.01_Codigo_Civil_texto_oficial (base Advoxs).pdf"


async def test_copia_em_dia_de_outro_agente_so_ganha_o_vinculo(session) -> None:
    document = _document()
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        public_document_id=document.id,
        status="ready",
        content_sha256=document.content_sha256,
    )
    session.scalars.side_effect = [_rows([document]), _rows([existing]), _rows([])]

    assert await packages.sync_agent_package(session, _agent()) == []

    assert _added(session, KnowledgeBaseFile) == []
    (link,) = _added(session, AgentKnowledgeBaseFile)
    assert link.knowledge_base_file_id == existing.id


async def test_copia_ja_ligada_e_em_dia_nao_muda(session) -> None:
    document = _document()
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        public_document_id=document.id,
        status="ready",
        content_sha256=document.content_sha256,
    )
    session.scalars.side_effect = [_rows([document]), _rows([existing]), _rows([existing.id])]

    assert await packages.sync_agent_package(session, _agent()) == []
    session.add.assert_not_called()


@pytest.mark.parametrize(
    ("status", "sha"), [("ready", "b" * 64), ("error", "a" * 64), ("processing", "a" * 64)]
)
async def test_copia_desatualizada_ou_incompleta_e_refeita(session, status, sha) -> None:
    document = _document(sha="a" * 64)
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        public_document_id=document.id,
        status=status,
        content_sha256=sha,
        error_message="falhou",
    )
    session.scalars.side_effect = [_rows([document]), _rows([existing]), _rows([existing.id])]

    assert await packages.sync_agent_package(session, _agent()) == [existing.id]

    assert existing.status == "processing"
    assert existing.content_sha256 == "a" * 64
    assert existing.error_message is None


async def test_trocar_de_especialidade_exclui_so_as_copias_sem_outro_agente(
    session, monkeypatch
) -> None:
    shared = SimpleNamespace(id=uuid.uuid4())
    orphan = SimpleNamespace(id=uuid.uuid4())
    session.scalars.side_effect = [_rows([shared, orphan]), _rows([shared.id])]
    delete_documents = AsyncMock()
    monkeypatch.setattr(packages, "delete_documents", delete_documents)

    await packages.detach_agent_package(session, _agent())

    delete_documents.assert_awaited_once_with(str(TENANT_ID), [str(orphan.id)])
    session.delete.assert_awaited_once_with(orphan)


async def test_falha_no_api_rag_nao_impede_a_troca(session, monkeypatch) -> None:
    orphan = SimpleNamespace(id=uuid.uuid4())
    session.scalars.side_effect = [_rows([orphan]), _rows([])]
    monkeypatch.setattr(packages, "delete_documents", AsyncMock(side_effect=RagApiError("x")))

    await packages.detach_agent_package(session, _agent())

    session.delete.assert_awaited_once_with(orphan)


async def test_agente_sem_copias_de_pacote_nao_chama_o_api_rag(session, monkeypatch) -> None:
    session.scalars.side_effect = [_rows([])]
    delete_documents = AsyncMock()
    monkeypatch.setattr(packages, "delete_documents", delete_documents)

    await packages.detach_agent_package(session, _agent())

    delete_documents.assert_not_awaited()
    session.execute.assert_not_awaited()


async def test_enfileira_uma_copia_por_arquivo_na_fila_de_ingestao() -> None:
    arq = AsyncMock()
    file_ids = [uuid.uuid4(), uuid.uuid4()]

    await packages.enqueue_copies(arq, TENANT_ID, file_ids)

    assert arq.enqueue_job.await_count == 2
    call = arq.enqueue_job.await_args_list[0]
    assert call.args == ("copy_public_document",)
    assert call.kwargs == {
        "tenant_id": str(TENANT_ID),
        "file_id": str(file_ids[0]),
        "_queue_name": "arq:ingestion",
    }
