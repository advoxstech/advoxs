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


def _service() -> DocumentoService:
    return DocumentoService(qdrant=MagicMock(), repo=MagicMock())


class TestExtrairTextoTxt:
    def test_txt_utf8(self) -> None:
        texto = _service()._extrair_texto("ação e direção".encode(), "txt")
        assert texto == "ação e direção"

    def test_txt_latin1_fallback(self) -> None:
        texto = _service()._extrair_texto("ação".encode("latin-1"), "txt")
        assert texto == "ação"


class TestInserirComDocIdExterno:
    @pytest.fixture
    def service(self, monkeypatch) -> DocumentoService:
        service = DocumentoService(qdrant=MagicMock(), repo=AsyncMock())
        sparse_vec = [{"indices": [0], "values": [1.0]}]
        monkeypatch.setattr(
            service,
            "_processar_documento",
            AsyncMock(return_value=("txt", "texto", ["texto"], [[0.1]], sparse_vec)),
        )
        monkeypatch.setattr(
            service, "_salvar_arquivo", MagicMock(return_value=("/base", "path"))
        )
        monkeypatch.setattr(service, "_salvar_qdrant", AsyncMock())
        monkeypatch.setattr(service, "deletar_documento_usuario", AsyncMock())
        return service

    def _file(self) -> MagicMock:
        file = MagicMock()
        file.filename = "regimento.txt"
        file.read = AsyncMock(return_value=b"conteudo")
        return file

    async def test_usa_doc_id_como_pk(self, service) -> None:
        doc_id = str(uuid.uuid4())
        service.repo.buscar_documento_usuario_por_id.return_value = None

        await service.inserir_documento_usuario([self._file()], "t1", "kb", doc_id=doc_id)

        instance = service.repo.criar_documento_usuario.await_args.args[0]
        assert str(instance.id) == doc_id
        service.deletar_documento_usuario.assert_not_awaited()

    async def test_doc_id_repetido_deleta_antes(self, service) -> None:
        doc_id = str(uuid.uuid4())
        service.repo.buscar_documento_usuario_por_id.return_value = MagicMock()

        await service.inserir_documento_usuario([self._file()], "t1", "kb", doc_id=doc_id)

        service.deletar_documento_usuario.assert_awaited_once_with("t1", [doc_id])

    async def test_sem_doc_id_mantem_default(self, service) -> None:
        service.repo.buscar_documento_usuario_por_id.return_value = None

        await service.inserir_documento_usuario([self._file()], "t1", "conversa-1")

        service.repo.buscar_documento_usuario_por_id.assert_not_awaited()


class TestReingestaoSubstituiArquivoNoDisco:
    """Interação real entre _salvar_arquivo e deletar_documento_usuario.

    Regressão: se o arquivo novo for gravado no disco ANTES da deleção do
    documento antigo (mesmo tenant + conversation + filename ⇒ mesmo path),
    a deleção apaga o arquivo recém-gravado.
    """

    async def test_arquivo_novo_sobrevive_a_reingestao(self, tmp_path, monkeypatch) -> None:
        from services.documents import main as documents_main

        monkeypatch.setattr(documents_main, "UPLOAD_DIR_USER", str(tmp_path))

        doc_id = str(uuid.uuid4())
        filename = "regimento.txt"
        caminho = tmp_path / "t1" / "kb" / filename

        # Arquivo antigo já no disco, da ingestão anterior com o mesmo doc_id.
        caminho.parent.mkdir(parents=True)
        caminho.write_bytes(b"conteudo antigo")

        doc_antigo = MagicMock()
        doc_antigo.id = uuid.UUID(doc_id)
        doc_antigo.tenant_id = "t1"
        doc_antigo.path_base = str(tmp_path)
        doc_antigo.path_doc = "t1/kb"
        doc_antigo.nome = filename

        repo = AsyncMock()
        repo.buscar_documento_usuario_por_id.return_value = doc_antigo

        service = DocumentoService(qdrant=AsyncMock(), repo=repo)
        sparse_vec = [{"indices": [0], "values": [1.0]}]
        monkeypatch.setattr(
            service,
            "_processar_documento",
            AsyncMock(return_value=("txt", "texto", ["texto"], [[0.1]], sparse_vec)),
        )
        monkeypatch.setattr(service, "_salvar_qdrant", AsyncMock())

        file = MagicMock()
        file.filename = filename
        file.read = AsyncMock(return_value=b"conteudo novo")

        await service.inserir_documento_usuario([file], "t1", "kb", doc_id=doc_id)

        # O arquivo novo tem que existir no disco após a re-ingestão.
        assert caminho.exists()
        assert caminho.read_bytes() == b"conteudo novo"
