import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from constants import QDRANT_COLLECTION
from services.documents import main as documents
from services.documents.main import DocumentoService

SOURCE_TENANT = "advoxs-public"
TENANT = "escritorio-1"
SOURCE_ID = uuid.uuid4()
DOC_ID = uuid.uuid4()


def _origem(tenant_id: str = SOURCE_TENANT):
    doc = MagicMock()
    doc.id = SOURCE_ID
    doc.tenant_id = tenant_id
    doc.nome = "1.01_Codigo_Civil.pdf"
    doc.extensao = "pdf"
    doc.path_base = "/tmp/nao-existe"
    doc.path_doc = f"{SOURCE_TENANT}/kb"
    return doc


def _ponto(texto: str):
    return SimpleNamespace(
        vector={"dense": [0.1, 0.2], "sparse": {"indices": [1], "values": [0.5]}},
        payload={
            "tenant_id": SOURCE_TENANT,
            "conversation_id": "kb",
            "doc_id": str(SOURCE_ID),
            "name": "1.01_Codigo_Civil.pdf",
            "text": texto,
            "page_start": 3,
            "page_end": 4,
        },
    )


@pytest.fixture
def qdrant():
    client = AsyncMock()
    client.upsert_points.return_value = {"success": True, "data": "ok", "error": None}
    return client


@pytest.fixture
def repo():
    mock = AsyncMock()
    mock.buscar_documento_usuario_por_id.side_effect = lambda doc_id: (
        _origem() if doc_id == SOURCE_ID else None
    )
    return mock


@pytest.fixture
def service(qdrant, repo, monkeypatch, tmp_path):
    monkeypatch.setattr(documents, "UPLOAD_DIR_USER", str(tmp_path))
    return DocumentoService(qdrant=qdrant, repo=repo)


async def _copiar(service, **extra):
    return await service.copiar_documento_usuario(
        source_tenant_id=SOURCE_TENANT,
        source_doc_id=str(SOURCE_ID),
        tenant_id=TENANT,
        conversation_id="kb",
        doc_id=str(DOC_ID),
        **extra,
    )


async def test_copia_os_trechos_com_o_escritorio_e_o_doc_id_de_destino(
    service, qdrant, repo
) -> None:
    qdrant.scroll_by_filter.side_effect = [
        ([_ponto("um"), _ponto("dois")], "proxima"),
        ([_ponto("tres")], None),
    ]

    assert await _copiar(service, filename="Codigo Civil.pdf") == 3

    primeira = qdrant.scroll_by_filter.await_args_list[0]
    assert primeira.args == (QDRANT_COLLECTION,)
    assert primeira.kwargs["tenant_id"] == SOURCE_TENANT
    assert primeira.kwargs["value"] == str(SOURCE_ID)
    assert qdrant.scroll_by_filter.await_args_list[1].kwargs["offset"] == "proxima"

    pontos = [p for call in qdrant.upsert_points.await_args_list for p in call.kwargs["points"]]
    assert [p.payload["text"] for p in pontos] == ["um", "dois", "tres"]
    for ponto in pontos:
        assert ponto.payload["tenant_id"] == TENANT
        assert ponto.payload["doc_id"] == str(DOC_ID)
        assert ponto.payload["name"] == "Codigo Civil.pdf"
        assert (ponto.payload["page_start"], ponto.payload["page_end"]) == (3, 4)
    assert len({p.id for p in pontos}) == 3

    registro = repo.criar_documento_usuario.await_args.args[0]
    assert (registro.id, registro.tenant_id, registro.conversation_id) == (DOC_ID, TENANT, "kb")
    assert registro.nome == "Codigo Civil.pdf"
    assert registro.path_doc == f"{TENANT}/kb"


async def test_origem_de_outro_escritorio_e_recusada(service, qdrant, repo) -> None:
    repo.buscar_documento_usuario_por_id.side_effect = lambda _: _origem(tenant_id="outro")

    with pytest.raises(ValueError, match="origem não encontrado"):
        await _copiar(service)

    qdrant.upsert_points.assert_not_awaited()
    repo.criar_documento_usuario.assert_not_awaited()


async def test_copiar_para_o_proprio_escritorio_e_recusado(service) -> None:
    with pytest.raises(ValueError, match="diferentes"):
        await service.copiar_documento_usuario(
            source_tenant_id=TENANT,
            source_doc_id=str(SOURCE_ID),
            tenant_id=TENANT,
            conversation_id="kb",
            doc_id=str(DOC_ID),
        )


async def test_refazer_a_copia_substitui_a_anterior(service, qdrant, repo) -> None:
    anterior = MagicMock()
    anterior.id = DOC_ID
    anterior.tenant_id = TENANT
    anterior.path_base, anterior.path_doc, anterior.nome = "/tmp/nao-existe", "x", "y.pdf"
    repo.buscar_documento_usuario_por_id.side_effect = lambda doc_id: (
        _origem() if doc_id == SOURCE_ID else anterior
    )
    qdrant.scroll_by_filter.return_value = ([_ponto("um")], None)

    await _copiar(service)

    qdrant.delete_points_by_filter.assert_awaited_once()
    assert qdrant.delete_points_by_filter.await_args.kwargs["tenant_id"] == TENANT
    repo.deletar_documento_usuario.assert_awaited_once_with(DOC_ID)


async def test_doc_id_de_outro_escritorio_nao_e_sobrescrito(service, qdrant, repo) -> None:
    alheio = MagicMock()
    alheio.tenant_id = "outro"
    repo.buscar_documento_usuario_por_id.side_effect = lambda doc_id: (
        _origem() if doc_id == SOURCE_ID else alheio
    )

    with pytest.raises(ValueError, match="outro escritório"):
        await _copiar(service)

    qdrant.delete_points_by_filter.assert_not_awaited()


async def test_falha_no_meio_desfaz_a_copia(service, qdrant, repo) -> None:
    criado = MagicMock()
    criado.id = DOC_ID
    criado.tenant_id = TENANT
    criado.path_base, criado.path_doc, criado.nome = "/tmp/nao-existe", "x", "y.pdf"
    chamadas = {"n": 0}

    def buscar(doc_id):
        if doc_id == SOURCE_ID:
            return _origem()
        chamadas["n"] += 1
        return None if chamadas["n"] == 1 else criado

    repo.buscar_documento_usuario_por_id.side_effect = buscar
    qdrant.scroll_by_filter.return_value = ([_ponto("um")], None)
    qdrant.upsert_points.return_value = {"success": False, "data": None, "error": "fora do ar"}

    with pytest.raises(RuntimeError, match="fora do ar"):
        await _copiar(service)

    qdrant.delete_points_by_filter.assert_awaited_once()
    repo.deletar_documento_usuario.assert_awaited_once_with(DOC_ID)


async def test_origem_sem_trechos_e_erro(service, qdrant) -> None:
    qdrant.scroll_by_filter.return_value = ([], None)

    with pytest.raises(ValueError, match="sem conteúdo"):
        await _copiar(service)


async def test_arquivo_original_e_ligado_sem_duplicar(service, qdrant, repo, tmp_path) -> None:
    pasta = tmp_path / SOURCE_TENANT / "kb"
    pasta.mkdir(parents=True)
    (pasta / "1.01_Codigo_Civil.pdf").write_bytes(b"%PDF conteudo")
    origem = _origem()
    origem.path_base = str(tmp_path)
    repo.buscar_documento_usuario_por_id.side_effect = lambda doc_id: (
        origem if doc_id == SOURCE_ID else None
    )
    qdrant.scroll_by_filter.return_value = ([_ponto("um")], None)

    await _copiar(service)

    copia = tmp_path / TENANT / "kb" / "1.01_Codigo_Civil.pdf"
    assert copia.read_bytes() == b"%PDF conteudo"
