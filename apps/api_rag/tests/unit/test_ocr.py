"""OCR das páginas escaneadas ou com texto embaralhado, dentro da leitura em lotes."""

import io
import shutil
from unittest.mock import AsyncMock, MagicMock

import pytest
from chonkie import RecursiveChunker

import services.documents.main as documents_main
from services.documents.main import DocumentoIlegivel, DocumentoService, _precisa_ocr
from tests.unit.test_ingestao_em_lotes import _pdf

TEXTO_BOM = (
    "CLÁUSULA 458. DAS OBRIGAÇÕES GERAIS. A contratada deverá comunicar eventuais "
    "alterações cadastrais em conformidade com a política de governança vigente."
)


def _service() -> DocumentoService:
    return DocumentoService(qdrant=MagicMock(), repo=MagicMock())


async def _coletar(service, dados: bytes, leitura: dict) -> list:
    return [s async for s in service._segmentos(dados, "pdf", leitura)]


class TestPrecisaOcr:
    def test_texto_normal_nao_precisa(self) -> None:
        assert _precisa_ocr(TEXTO_BOM) is False

    @pytest.mark.parametrize("texto", ["", "   \n ", "Página 3 de 50"])
    def test_pagina_sem_texto_precisa(self, texto) -> None:
        assert _precisa_ocr(texto) is True

    def test_fonte_sem_mapa_unicode_precisa(self) -> None:
        # Trecho real do UM050: letras deslocadas e caracteres de controle.
        embaralhado = "$'92;6\x107(67(_'RFXPHQWR80\x13\x18\x13 &/È868/$\x17\x18\x1b\x11'$62%5,*$"
        assert _precisa_ocr(embaralhado * 3) is True

    def test_letras_sem_vogais_precisa(self) -> None:
        assert _precisa_ocr("FRQWUDWDGDGHYHU FRPXQLFDU HYHQWXDLV DOWHUDFRHV " * 3) is True

    def test_pagina_de_numeros_fica_com_texto_nativo(self) -> None:
        assert _precisa_ocr("1.250,00 3.400,00 12/03/2024 15/04/2024 R$ 9.870,55 " * 2) is False


@pytest.fixture
def ocr(monkeypatch) -> AsyncMock:
    falso = AsyncMock(return_value="texto lido pelo ocr da pagina escaneada")
    monkeypatch.setattr(documents_main, "_ocr_imagem", falso)
    monkeypatch.setattr(documents_main, "_renderizar_pagina", lambda pdf, indice: b"png")
    return falso


class TestSegmentosComOcr:
    async def test_ocr_so_nas_paginas_sem_texto(self, ocr) -> None:
        leitura: dict = {}

        segmentos = await _coletar(_service(), _pdf([TEXTO_BOM, "", TEXTO_BOM, ""]), leitura)

        texto = "\n".join(s.texto for s in segmentos)
        assert ocr.await_count == 2
        assert texto.count("texto lido pelo ocr") == 2
        # O gerador de PDF de teste não codifica acentos; o texto nativo segue intacto.
        assert "contratada" in texto
        assert leitura == {"paginas": 4, "ocr": 2, "ilegiveis": 0}

    async def test_pagina_que_nem_o_ocr_le_e_contada(self, ocr) -> None:
        ocr.return_value = "   "
        leitura: dict = {}

        await _coletar(_service(), _pdf([TEXTO_BOM, ""]), leitura)

        assert leitura["ilegiveis"] == 1

    async def test_escaneadas_demais_recusa_antes_de_ler(self, ocr, monkeypatch) -> None:
        monkeypatch.setattr(documents_main, "MAX_OCR_PAGES", 1)

        with pytest.raises(DocumentoIlegivel, match="2 páginas escaneadas"):
            await _coletar(_service(), _pdf(["", "", TEXTO_BOM]), {})

        ocr.assert_not_awaited()

    async def test_ocr_desligado_nao_le_imagem(self, ocr, monkeypatch) -> None:
        monkeypatch.setattr(documents_main, "OCR_ENABLED", False)

        await _coletar(_service(), _pdf([TEXTO_BOM, ""]), {})

        ocr.assert_not_awaited()


@pytest.fixture
def indexador(monkeypatch) -> DocumentoService:
    service = DocumentoService(qdrant=AsyncMock(), repo=AsyncMock())
    service.qdrant.upsert_points.return_value = {"success": True}
    service.chunker = RecursiveChunker(chunk_size=60)
    service.emb_model = MagicMock()
    service.emb_model.aembed_documents = AsyncMock(side_effect=lambda t: [[0.1]] * len(t))
    sparse = AsyncMock(side_effect=lambda t, _: [{"indices": [1], "values": [1.0]}] * len(t))
    monkeypatch.setattr(service, "get_sparse_embeddings_batch", sparse)
    return service


class TestIndexarComOcr:
    async def test_texto_do_ocr_vira_trecho_com_pagina(self, indexador, ocr) -> None:
        await indexador._indexar(_pdf(["", TEXTO_BOM]), "pdf", "c", {"tenant_id": "t"})

        pontos = [
            p for c in indexador.qdrant.upsert_points.await_args_list for p in c.kwargs["points"]
        ]
        lidos = [p for p in pontos if "texto lido pelo ocr" in p.payload["text"]]
        assert lidos and lidos[0].payload["page_start"] == 1

    async def test_nada_legivel_nem_com_ocr(self, indexador, ocr) -> None:
        ocr.return_value = ""

        with pytest.raises(DocumentoIlegivel, match="nem com OCR"):
            await indexador._indexar(_pdf(["", ""]), "pdf", "c", {"tenant_id": "t"})


class TestOcrImagem:
    async def test_le_as_duas_orientacoes_e_fica_com_a_que_faz_sentido(self, monkeypatch) -> None:
        async def tesseract(argumentos, imagem):
            if argumentos[-1] == "0":
                return "Orientation in degrees: 180\nRotate: 180\n"
            return "a contratada deverá comunicar o conselho" if imagem == b"girada" else "zx qw"

        monkeypatch.setattr(documents_main, "_tesseract", tesseract)
        monkeypatch.setattr(documents_main, "_girar", lambda imagem, graus: b"girada")

        assert await documents_main._ocr_imagem(b"original") == (
            "a contratada deverá comunicar o conselho"
        )

    async def test_orientacao_errada_nao_estraga_pagina_em_pe(self, monkeypatch) -> None:
        async def tesseract(argumentos, imagem):
            if argumentos[-1] == "0":
                return "Rotate: 180\n"
            return "a contratada deverá comunicar o conselho" if imagem == b"original" else "zx"

        monkeypatch.setattr(documents_main, "_tesseract", tesseract)
        monkeypatch.setattr(documents_main, "_girar", lambda imagem, graus: b"girada")

        assert await documents_main._ocr_imagem(b"original") == (
            "a contratada deverá comunicar o conselho"
        )

    async def test_sem_tesseract_devolve_vazio(self, monkeypatch) -> None:
        monkeypatch.setattr(
            documents_main.asyncio,
            "create_subprocess_exec",
            AsyncMock(side_effect=FileNotFoundError("tesseract")),
        )

        assert await documents_main._ocr_imagem(b"png") == ""


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract não instalado")
async def test_pdf_escaneado_de_verdade_e_lido_pelo_tesseract() -> None:
    from PIL import Image, ImageDraw, ImageFont

    pagina = Image.new("L", (1700, 700), 255)
    desenho = ImageDraw.Draw(pagina)
    fonte = ImageFont.load_default(size=56)
    desenho.text((80, 120), "CONTRATO DE PRESTAÇÃO DE SERVIÇOS", fill=0, font=fonte)
    desenho.text((80, 260), "O prazo de carência do contrato", fill=0, font=fonte)
    desenho.text((80, 400), "Zeta-7 é de 43 dias.", fill=0, font=fonte)
    escaneado = io.BytesIO()
    pagina.save(escaneado, format="PDF", resolution=200)

    leitura: dict = {}
    segmentos = await _coletar(_service(), escaneado.getvalue(), leitura)

    texto = " ".join(" ".join(s.texto for s in segmentos).split())
    assert leitura["ocr"] == 1
    assert "CONTRATO" in texto
    assert "43 dias" in texto
