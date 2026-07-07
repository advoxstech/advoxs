import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from constants import QDRANT_COLLECTION, SYSTEM_TENANT_ID
from services.documents.main import DocumentoService


@pytest.fixture
def qdrant():
    return AsyncMock()


@pytest.fixture
def repo():
    return AsyncMock()


@pytest.fixture
def service(qdrant, repo):
    return DocumentoService(qdrant=qdrant, repo=repo)


def _doc(tenant_id: str = "t1"):
    doc = MagicMock()
    doc.id = uuid.uuid4()
    doc.tenant_id = tenant_id
    doc.path_base = "/tmp/nao-existe"
    doc.path_doc = "t1/c1"
    doc.nome = "arquivo.pdf"
    return doc


class TestSalvarQdrant:
    async def test_payload_ganha_chave_text(self, service, qdrant) -> None:
        await service._salvar_qdrant(
            ["chunk um", "chunk dois"],
            [[0.1], [0.2]],
            [{"indices": [1], "values": [0.5]}, {"indices": [2], "values": [0.7]}],
            {"tenant_id": "t1", "conversation_id": "c1"},
        )

        qdrant.upsert_points.assert_awaited_once()
        kwargs = qdrant.upsert_points.await_args.kwargs
        assert kwargs["collection_name"] == QDRANT_COLLECTION
        points = kwargs["points"]
        assert len(points) == 2
        # O retrieval lê payload["text"] — a chave gravada tem que ser essa.
        assert points[0].payload["text"] == "chunk um"
        assert points[0].payload["tenant_id"] == "t1"

    async def test_sem_tenant_id_aborta(self, service, qdrant) -> None:
        with pytest.raises(ValueError, match="tenant_id"):
            await service._salvar_qdrant(["chunk"], [[0.1]], [{}], {"conversation_id": "c1"})

        qdrant.upsert_points.assert_not_awaited()


class TestDeletarDocumentoUsuario:
    async def test_deleta_com_filtro_de_tenant(self, service, qdrant, repo) -> None:
        doc = _doc(tenant_id="t1")
        repo.buscar_documento_usuario_por_id.return_value = doc

        await service.deletar_documento_usuario("t1", [str(doc.id)])

        qdrant.delete_points_by_filter.assert_awaited_once_with(
            collection_name=QDRANT_COLLECTION,
            tenant_id="t1",
            field="doc_id",
            value=str(doc.id),
        )
        repo.deletar_documento_usuario.assert_awaited_once_with(doc.id)

    async def test_documento_de_outro_tenant_nao_deleta(self, service, qdrant, repo) -> None:
        doc = _doc(tenant_id="outro-tenant")
        repo.buscar_documento_usuario_por_id.return_value = doc

        await service.deletar_documento_usuario("t1", [str(doc.id)])

        qdrant.delete_points_by_filter.assert_not_awaited()
        repo.deletar_documento_usuario.assert_not_awaited()

    async def test_documento_inexistente_nao_deleta(self, service, qdrant, repo) -> None:
        repo.buscar_documento_usuario_por_id.return_value = None

        await service.deletar_documento_usuario("t1", [str(uuid.uuid4())])

        qdrant.delete_points_by_filter.assert_not_awaited()
        repo.deletar_documento_usuario.assert_not_awaited()

    async def test_sem_tenant_id_levanta_erro(self, service, repo) -> None:
        with pytest.raises(ValueError, match="tenant_id"):
            await service.deletar_documento_usuario("", [str(uuid.uuid4())])

        repo.buscar_documento_usuario_por_id.assert_not_awaited()


class TestDeletarDocumentoSistema:
    async def test_deleta_com_tenant_reservado(self, service, qdrant, repo) -> None:
        doc = _doc()
        repo.buscar_documento_sistema_por_id.return_value = doc

        await service.deletar_documento_sistema([str(doc.id)])

        qdrant.delete_points_by_filter.assert_awaited_once_with(
            collection_name=QDRANT_COLLECTION,
            tenant_id=SYSTEM_TENANT_ID,
            field="doc_id",
            value=str(doc.id),
        )
        repo.deletar_documento_sistema.assert_awaited_once_with(doc.id)


class TestInserirDocumentoUsuario:
    async def test_sem_tenant_id_levanta_erro(self, service) -> None:
        with pytest.raises(ValueError, match="tenant_id"):
            await service.inserir_documento_usuario([], "", "c1")
