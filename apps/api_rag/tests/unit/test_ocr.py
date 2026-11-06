"""OCR das páginas escaneadas ou com texto embaralhado, dentro da leitura em lotes."""

import asyncio
import io
import shutil
from unittest.mock import AsyncMock, MagicMock

import pytest
from chonkie import RecursiveChunker

import services.documents.main as documents_main
from services.documents.main import (
    DocumentoIlegivel,
    DocumentoService,
    _LeituraOcr,
    _ler_tsv,
    _precisa_ocr,
)
from tests.unit.test_ingestao_em_lotes import _pdf

TEXTO_BOM = (
    "CLÁUSULA 458. DAS OBRIGAÇÕES GERAIS. A contratada deverá comunicar eventuais "
    "alterações cadastrais em conformidade com a política de governança vigente."
)


_CABECALHO_TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)


def _tsv(texto: str, confianca: float = 95) -> str:
    """Saída TSV do Tesseract: cada linha do texto vira uma linha da página."""
    registros = [_CABECALHO_TSV]
    for numero, linha in enumerate(texto.splitlines(), start=1):
        for posicao, palavra in enumerate(linha.split(), start=1):
            registros.append(f"5\t1\t1\t1\t{numero}\t{posicao}\t0\t0\t1\t1\t{confianca}\t{palavra}")
    return "\n".join(registros)


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
    falso = AsyncMock(return_value=_LeituraOcr("texto lido pelo ocr da pagina escaneada"))
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
        assert leitura == {
            "paginas": 4,
            "ocr": 2,
            "ilegiveis": 0,
            "sem_texto": [],
            "fracas": [],
        }

    async def test_pagina_que_nem_o_ocr_le_e_contada(self, ocr) -> None:
        ocr.return_value = _LeituraOcr("   ")
        leitura: dict = {}

        await _coletar(_service(), _pdf([TEXTO_BOM, ""]), leitura)

        assert leitura["ilegiveis"] == 1
        assert leitura["sem_texto"] == [2]

    async def test_leitura_fraca_marca_a_pagina(self, ocr) -> None:
        ocr.return_value = _LeituraOcr("palavra " * 50, confianca=45, palavras=50)
        leitura: dict = {}

        await _coletar(_service(), _pdf([TEXTO_BOM, "", ""]), leitura)

        assert leitura["fracas"] == [2, 3]
        assert documents_main.resumo_leitura(leitura)["paginas_parciais"] == [2, 3]

    async def test_pagina_curta_nao_e_julgada(self, ocr) -> None:
        ocr.return_value = _LeituraOcr("assinatura", confianca=30, palavras=1)
        leitura: dict = {}

        await _coletar(_service(), _pdf([""]), leitura)

        assert leitura["fracas"] == []

    async def test_ocr_sem_leitura_mantem_texto_nativo_curto(self, ocr) -> None:
        ocr.return_value = _LeituraOcr("")

        segmentos = await _coletar(_service(), _pdf(["Pagina 3 de 50"]), {})

        assert "Pagina 3 de 50" in segmentos[0].texto

    async def test_ocr_sem_leitura_descarta_texto_embaralhado(self, ocr, monkeypatch) -> None:
        ocr.return_value = _LeituraOcr("")
        monkeypatch.setattr(documents_main, "_precisa_ocr", lambda texto: True)

        segmentos = await _coletar(_service(), _pdf([TEXTO_BOM]), {})

        assert segmentos[0].texto.strip() == ""

    async def test_escaneadas_demais_recusa_antes_de_ler(self, ocr, monkeypatch) -> None:
        monkeypatch.setattr(documents_main, "MAX_OCR_PAGES", 1)

        with pytest.raises(DocumentoIlegivel, match="2 páginas escaneadas"):
            await _coletar(_service(), _pdf(["", "", TEXTO_BOM]), {})

        ocr.assert_not_awaited()

    async def test_ocr_desligado_nao_le_imagem(self, ocr, monkeypatch) -> None:
        monkeypatch.setattr(documents_main, "OCR_ENABLED", False)

        await _coletar(_service(), _pdf([TEXTO_BOM, ""]), {})

        ocr.assert_not_awaited()


def _ocr_contando(limite: int, monkeypatch) -> dict:
    """OCR falso que mede o pico de leituras simultâneas e devolve o índice da página."""
    estado = {"agora": 0, "pico": 0}
    monkeypatch.setattr(documents_main, "_ocr_slots", asyncio.Semaphore(limite))
    monkeypatch.setattr(documents_main, "_renderizar_pagina", lambda pdf, indice: indice)

    async def ocr(indice):
        estado["agora"] += 1
        estado["pico"] = max(estado["pico"], estado["agora"])
        await asyncio.sleep(0.01 * (5 - indice % 5))  # termina fora de ordem
        estado["agora"] -= 1
        return _LeituraOcr(f"pagina lida numero {indice + 1}")

    monkeypatch.setattr(documents_main, "_ocr_imagem", ocr)
    return estado


class TestOcrEmParalelo:
    @pytest.mark.parametrize("limite", [1, 2, 3])
    async def test_respeita_o_limite_de_ocrs_simultaneos(self, monkeypatch, limite) -> None:
        estado = _ocr_contando(limite, monkeypatch)

        await _coletar(_service(), _pdf([""] * 8), {})

        assert estado["pico"] == limite

    async def test_texto_volta_na_ordem_das_paginas(self, monkeypatch) -> None:
        _ocr_contando(2, monkeypatch)
        leitura: dict = {}

        segmentos = await _coletar(_service(), _pdf(["", TEXTO_BOM, "", "", ""]), leitura)

        texto = segmentos[0].texto
        posicoes = [texto.index(f"pagina lida numero {n}") for n in (1, 3, 4, 5)]
        assert posicoes == sorted(posicoes)
        assert "contratada" in texto.split("pagina lida numero 3")[0]
        assert segmentos[0].pagina_em(texto.index("numero 4")) == 4
        assert leitura["ocr"] == 4

    async def test_falha_em_uma_pagina_interrompe_o_documento(self, monkeypatch) -> None:
        _ocr_contando(2, monkeypatch)

        async def falha(indice):
            if indice == 2:
                raise RuntimeError("pdfium")
            await asyncio.sleep(0.05)
            return _LeituraOcr("ok")

        monkeypatch.setattr(documents_main, "_ocr_imagem", falha)

        with pytest.raises(ExceptionGroup):
            await _coletar(_service(), _pdf([""] * 4), {})


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

    async def test_leitura_parcial_marca_os_trechos_do_documento(self, indexador, ocr) -> None:
        ocr.return_value = _LeituraOcr("")
        leitura: dict = {}

        await indexador._indexar(
            _pdf([TEXTO_BOM, ""]), "pdf", "c", {"tenant_id": "t", "doc_id": "d"}, leitura
        )

        indexador.qdrant.set_payload_by_filter.assert_awaited_once()
        chamada = indexador.qdrant.set_payload_by_filter.await_args.kwargs
        assert chamada["tenant_id"] == "t"
        assert chamada["value"] == "d"
        assert chamada["payload"] == {"partial_pages": [2], "pages_total": 2}

    async def test_leitura_completa_nao_marca_nada(self, indexador, ocr) -> None:
        payload = {"tenant_id": "t", "doc_id": "d"}

        await indexador._indexar(_pdf([TEXTO_BOM]), "pdf", "c", payload)

        indexador.qdrant.set_payload_by_filter.assert_not_awaited()

    async def test_nada_legivel_nem_com_ocr(self, indexador, ocr) -> None:
        ocr.return_value = _LeituraOcr("")

        with pytest.raises(DocumentoIlegivel, match="nem com OCR"):
            await indexador._indexar(_pdf(["", ""]), "pdf", "c", {"tenant_id": "t"})


class TestOcrImagem:
    async def test_le_as_duas_orientacoes_e_fica_com_a_que_faz_sentido(self, monkeypatch) -> None:
        async def tesseract(argumentos, imagem):
            if argumentos[-1] == "0":
                return "Orientation in degrees: 180\nRotate: 180\n"
            boa = "a contratada deverá comunicar o conselho"
            return _tsv(boa) if imagem == b"girada" else _tsv("zx qw", 20)

        monkeypatch.setattr(documents_main, "_tesseract", tesseract)
        monkeypatch.setattr(documents_main, "_girar", lambda imagem, graus: b"girada")

        lido = await documents_main._ocr_imagem(b"original")
        assert lido.texto == "a contratada deverá comunicar o conselho"
        assert lido.confianca == 95

    async def test_leitura_confiavel_nao_detecta_orientacao(self, monkeypatch) -> None:
        chamadas: list[str] = []

        async def tesseract(argumentos, imagem):
            chamadas.append(argumentos[-1])
            return _tsv("a contratada deverá comunicar o conselho", 95)

        monkeypatch.setattr(documents_main, "_tesseract", tesseract)

        lido = await documents_main._ocr_imagem(b"original")

        assert lido.confianca == 95
        assert chamadas == ["tsv"]

    async def test_leitura_vazia_detecta_orientacao(self, monkeypatch) -> None:
        chamadas: list[str] = []

        async def tesseract(argumentos, imagem):
            chamadas.append(argumentos[-1])
            return "" if argumentos[-1] == "tsv" else "Rotate: 0\n"

        monkeypatch.setattr(documents_main, "_tesseract", tesseract)

        lido = await documents_main._ocr_imagem(b"original")

        assert lido.texto == ""
        assert chamadas == ["tsv", "0"]

    async def test_orientacao_errada_nao_estraga_pagina_em_pe(self, monkeypatch) -> None:
        async def tesseract(argumentos, imagem):
            if argumentos[-1] == "0":
                return "Rotate: 180\n"
            boa = "a contratada deverá comunicar o conselho"
            return _tsv(boa, 60) if imagem == b"original" else _tsv("zx", 60)

        monkeypatch.setattr(documents_main, "_tesseract", tesseract)
        monkeypatch.setattr(documents_main, "_girar", lambda imagem, graus: b"girada")

        lido = await documents_main._ocr_imagem(b"original")
        assert lido.texto == "a contratada deverá comunicar o conselho"

    async def test_sem_tesseract_devolve_vazio(self, monkeypatch) -> None:
        monkeypatch.setattr(
            documents_main.asyncio,
            "create_subprocess_exec",
            AsyncMock(side_effect=FileNotFoundError("tesseract")),
        )

        assert (await documents_main._ocr_imagem(b"png")).texto == ""


class TestLerTsv:
    def test_preserva_linhas_e_paragrafos(self) -> None:
        tsv = "\n".join(
            [
                _CABECALHO_TSV,
                "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t96\tCLÁUSULA",
                "5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t90\t1.",
                "5\t1\t1\t1\t2\t1\t0\t0\t1\t1\t94\tprazo",
                "4\t1\t1\t2\t1\t0\t0\t0\t1\t1\t-1\t",
                "5\t1\t1\t2\t1\t1\t0\t0\t1\t1\t80\tfim",
            ]
        )

        lido = _ler_tsv(tsv)

        assert lido.texto == "CLÁUSULA 1.\nprazo\n\nfim"
        # "1." não tem letras: fica fora da média.
        assert lido.confianca == (96 + 94 + 80) / 3
        assert lido.palavras == 3

    def test_saida_vazia(self) -> None:
        assert _ler_tsv("") == _LeituraOcr("", None, 0)

    def test_leitura_fraca_exige_palavras_suficientes(self) -> None:
        assert _LeituraOcr("x", confianca=40, palavras=200).fraca is True
        assert _LeituraOcr("x", confianca=40, palavras=5).fraca is False
        assert _LeituraOcr("x", confianca=95, palavras=200).fraca is False


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
    assert leitura["fracas"] == []

    texto = " ".join(" ".join(s.texto for s in segmentos).split())
    assert leitura["ocr"] == 1
    assert "CONTRATO" in texto
    assert "43 dias" in texto
