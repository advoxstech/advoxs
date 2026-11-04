"""Ingestão em lotes: memória constante, páginas preservadas e nada pela metade."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from chonkie import RecursiveChunker

import services.documents.main as documents_main
from services.documents.main import DocumentoIlegivel, DocumentoService, _Segmento


def _pdf(paginas: list[str]) -> bytes:
    """PDF mínimo e válido, uma linha de texto por página."""
    objetos: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(paginas)))
    objetos.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objetos.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(paginas)} >>".encode())
    fonte = 3 + 2 * len(paginas)
    for i, texto in enumerate(paginas):
        conteudo = f"BT /F1 12 Tf 50 700 Td ({texto}) Tj ET".encode()
        objetos.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {fonte} 0 R >> >> >>".encode()
        )
        objetos.append(
            b"<< /Length "
            + str(len(conteudo)).encode()
            + b" >>\nstream\n"
            + conteudo
            + b"\nendstream"
        )
    objetos.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    saida = bytearray(b"%PDF-1.4\n")
    offsets = []
    for numero, corpo in enumerate(objetos, start=1):
        offsets.append(len(saida))
        saida += f"{numero} 0 obj\n".encode() + corpo + b"\nendobj\n"
    xref = len(saida)
    saida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        saida += f"{offset:010d} 00000 n \n".encode()
    saida += (
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(saida)


def _service() -> DocumentoService:
    return DocumentoService(qdrant=MagicMock(), repo=MagicMock())


async def _coletar(service: DocumentoService, dados: bytes, extensao: str) -> list[_Segmento]:
    return [s async for s in service._segmentos(dados, extensao)]


class TestSegmento:
    def test_pagina_em_e_recorte(self) -> None:
        seg = _Segmento("aaaa\nbbbb\ncccc", [(0, 1), (5, 2), (10, 3)])

        assert seg.pagina_em(0) == 1
        assert seg.pagina_em(7) == 2
        resto = seg.a_partir_de(7)
        assert resto.texto == "bb\ncccc"
        assert resto.paginas == [(0, 2), (3, 3)]

    def test_emenda_desloca_paginas(self) -> None:
        juntos = _Segmento("abc", [(0, 1)]).seguido_de(_Segmento("de", [(0, 2)]))

        assert juntos.texto == "abc\nde"
        assert juntos.paginas == [(0, 1), (4, 2)]

    def test_fatiar_texto_respeita_quebra_de_linha(self, monkeypatch) -> None:
        monkeypatch.setattr(documents_main, "INGEST_TEXT_BATCH_CHARS", 10)

        lotes = documents_main._fatiar_texto("linha um\nlinha dois\nfim")

        assert "".join(lotes) == "linha um\nlinha dois\nfim"
        assert lotes[0] == "linha um"


class TestSegmentos:
    async def test_pdf_lido_em_lotes_de_paginas(self, monkeypatch) -> None:
        monkeypatch.setattr(documents_main, "INGEST_PAGE_BATCH", 2)

        segmentos = await _coletar(
            _service(), _pdf(["um", "dois", "tres", "quatro", "cinco"]), "pdf"
        )

        assert len(segmentos) == 3
        assert [n for _, n in segmentos[1].paginas] == [3, 4]
        assert "quatro" in segmentos[1].texto

    async def test_txt_utf8_e_latin1(self) -> None:
        utf8 = await _coletar(_service(), "ação e direção".encode(), "txt")
        latin = await _coletar(_service(), "ação".encode("latin-1"), "txt")

        assert utf8[0].texto == "ação e direção"
        assert latin[0].texto == "ação"

    async def test_pdf_com_senha_tem_motivo_claro(self, monkeypatch) -> None:
        def protegido(_):
            raise documents_main.pdfium.PdfiumError(
                "Failed to load document (PDFium: Incorrect password error)."
            )

        monkeypatch.setattr(documents_main.pdfium, "PdfDocument", protegido)

        with pytest.raises(DocumentoIlegivel, match="senha"):
            await _coletar(_service(), b"%PDF", "pdf")

    async def test_pdf_corrompido(self) -> None:
        with pytest.raises(DocumentoIlegivel, match="corrompido"):
            await _coletar(_service(), b"isto nao e um pdf", "pdf")

    async def test_pdf_com_paginas_demais(self, monkeypatch) -> None:
        monkeypatch.setattr(documents_main, "MAX_PDF_PAGES", 2)

        with pytest.raises(DocumentoIlegivel, match="3 páginas"):
            await _coletar(_service(), _pdf(["a", "b", "c"]), "pdf")


@pytest.fixture
def indexador(monkeypatch) -> DocumentoService:
    service = DocumentoService(qdrant=AsyncMock(), repo=AsyncMock())
    service.qdrant.upsert_points.return_value = {"success": True, "data": "ok", "error": None}
    service.chunker = RecursiveChunker(chunk_size=40)
    service.emb_model = MagicMock()
    service.emb_model.aembed_documents = AsyncMock(side_effect=lambda t: [[0.1]] * len(t))
    sparse = AsyncMock(side_effect=lambda t, _: [{"indices": [1], "values": [1.0]}] * len(t))
    monkeypatch.setattr(service, "get_sparse_embeddings_batch", sparse)
    monkeypatch.setattr(documents_main, "INGEST_PAGE_BATCH", 2)
    return service


def _pontos(service: DocumentoService) -> list:
    return [
        ponto
        for call in service.qdrant.upsert_points.await_args_list
        for ponto in call.kwargs["points"]
    ]


class TestIndexar:
    async def test_grava_por_lote_sem_perder_texto_e_com_paginas(self, indexador) -> None:
        paginas = [f"Pagina {n} fala sobre clausula {n} do contrato" for n in range(1, 6)]

        total = await indexador._indexar(_pdf(paginas), "pdf", "conv", {"tenant_id": "t1"})

        pontos = _pontos(indexador)
        assert total == len(pontos)
        # Mais de uma gravação: o documento não foi gravado de uma vez só.
        assert indexador.qdrant.upsert_points.await_count >= 2
        texto = " ".join(p.payload["text"] for p in pontos)
        for n in range(1, 6):
            assert f"clausula {n}" in texto
        assert {p.payload["page_start"] for p in pontos} == {1, 2, 3, 4, 5}
        assert all(p.payload["page_start"] <= p.payload["page_end"] for p in pontos)

    async def test_pdf_sem_texto_e_recusado(self, indexador) -> None:
        with pytest.raises(DocumentoIlegivel, match="OCR"):
            await indexador._indexar(_pdf(["", ""]), "pdf", "conv", {"tenant_id": "t1"})

        indexador.qdrant.upsert_points.assert_not_awaited()

    async def test_txt_sem_numero_de_pagina(self, indexador) -> None:
        await indexador._indexar(b"texto simples do arquivo", "txt", "c", {"tenant_id": "t"})

        assert "page_start" not in _pontos(indexador)[0].payload


class TestLotesExternos:
    async def test_sparse_em_lotes_de_32(self, monkeypatch) -> None:
        client = AsyncMock()

        async def post(url, json):
            resposta = MagicMock()
            resposta.json.return_value = {"vectors": [{}] * len(json["texts"])}
            return resposta

        client.post.side_effect = post
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(documents_main.httpx, "AsyncClient", MagicMock(return_value=cm))

        vetores = await _service().get_sparse_embeddings_batch([f"t{i}" for i in range(70)], "doc")

        tamanhos = [len(c.kwargs["json"]["texts"]) for c in client.post.await_args_list]
        assert tamanhos == [32, 32, 6]
        assert len(vetores) == 70

    async def test_upsert_em_lotes(self) -> None:
        service = DocumentoService(qdrant=AsyncMock(), repo=AsyncMock())
        service.qdrant.upsert_points.return_value = {"success": True}

        await service._salvar_qdrant(
            [f"t{i}" for i in range(130)],
            [[0.1]] * 130,
            [{"indices": [1], "values": [1.0]}] * 130,
            {"tenant_id": "t1"},
        )

        lotes = [len(c.kwargs["points"]) for c in service.qdrant.upsert_points.await_args_list]
        assert lotes == [64, 64, 2]


class TestInserirDocumentoUsuario:
    @pytest.fixture
    def service(self, monkeypatch) -> DocumentoService:
        service = DocumentoService(qdrant=MagicMock(), repo=AsyncMock())
        monkeypatch.setattr(service, "_indexar", AsyncMock(return_value=3))
        monkeypatch.setattr(service, "_salvar_arquivo", MagicMock(return_value=("/base", "path")))
        monkeypatch.setattr(service, "deletar_documento_usuario", AsyncMock())
        service.repo.criar_documento_usuario.side_effect = lambda instance: instance
        return service

    def _file(self, name: str = "regimento.txt") -> MagicMock:
        file = MagicMock()
        file.filename = name
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
        await service.inserir_documento_usuario([self._file()], "t1", "conversa-1")

        service.repo.buscar_documento_usuario_por_id.assert_not_awaited()

    async def test_falha_no_meio_desfaz_tudo(self, service) -> None:
        doc_id = str(uuid.uuid4())
        service.repo.buscar_documento_usuario_por_id.return_value = None
        service._indexar.side_effect = RuntimeError("Qdrant caiu no lote 3")

        with pytest.raises(RuntimeError):
            await service.inserir_documento_usuario([self._file()], "t1", "kb", doc_id=doc_id)

        service.deletar_documento_usuario.assert_awaited_once_with("t1", [doc_id])

    async def test_formato_nao_suportado_nao_grava_nada(self, service) -> None:
        with pytest.raises(ValueError, match="Formato"):
            await service.inserir_documento_usuario([self._file("foto.png")], "t1", "kb")

        service._salvar_arquivo.assert_not_called()
        service.repo.criar_documento_usuario.assert_not_awaited()
