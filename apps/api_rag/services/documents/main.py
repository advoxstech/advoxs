import asyncio
import io
import os
import re
import unicodedata
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pypdfium2 as pdfium
from chonkie import RecursiveChunker
from docx import Document
from dotenv import load_dotenv
from fastapi import UploadFile
from langchain_openai import OpenAIEmbeddings
from loguru import logger
from qdrant_client.models import PointStruct, SparseVector

from clients.qdrant import QdrantClient
from constants import (
    DENSE_BATCH,
    INGEST_CONCURRENCY,
    INGEST_PAGE_BATCH,
    INGEST_TEXT_BATCH_CHARS,
    MAX_OCR_PAGES,
    MAX_PDF_PAGES,
    OCR_CONCURRENCY,
    OCR_DPI,
    OCR_ENABLED,
    OCR_LANGUAGES,
    OCR_MAX_PIXELS,
    OCR_MIN_CONFIDENCE,
    OCR_MIN_PAGE_CHARS,
    OCR_MIN_WORDS_TO_JUDGE,
    OCR_PAGE_TIMEOUT_SECONDS,
    QDRANT_COLLECTION,
    QDRANT_UPSERT_BATCH,
    SPARSE_BATCH,
    SYSTEM_TENANT_ID,
)
from database.models import DocumentoSistema, DocumentoUsuario
from database.repositories.documento import DocumentoRepository
from safe_logging import safe_identifier

load_dotenv()

# ---------- CONFIG ----------

DENSE_MODEL = os.getenv("DENSE_MODEL")
URL_API_LOCAL_SPARSE = os.getenv("URL_API_LOCAL_SPARSE")
UPLOAD_DIR_USER = os.getenv("UPLOAD_DIR_USER")
UPLOAD_DIR_SYSTEM = os.getenv("UPLOAD_DIR_SYSTEM")
SUPPORTED_EXTENSIONS = {"pdf", "docx", "txt"}

# ---------- MODELOS (carregados uma vez) ----------

_emb_model = OpenAIEmbeddings(model=DENSE_MODEL, chunk_size=DENSE_BATCH)
_chunker = RecursiveChunker()
# O PDFium não é thread-safe: toda chamada a ele passa por esta única thread,
# o que também tira a extração (CPU) do event loop que atende as buscas.
_pdfium_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdfium")
# Documentos processados ao mesmo tempo; os demais pedidos esperam a vez.
_ingest_slots = asyncio.Semaphore(INGEST_CONCURRENCY)
# OCRs simultâneos em todo o processo (padrão 2, um por núcleo da VPS). Cada Tesseract
# usa 1 núcleo com prioridade baixa: o atendimento sempre passa na frente.
_ocr_slots = asyncio.Semaphore(OCR_CONCURRENCY)
# Caracteres de controle (fora tab/quebras) indicam fonte sem mapa Unicode.
_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class DocumentoIlegivel(ValueError):
    """Arquivo que não dá para ler: senha, corrompido, sem texto ou páginas demais."""


@dataclass
class _Segmento:
    """Trecho contínuo do documento e o ponto do texto onde cada página começa."""

    texto: str
    # (offset no texto, número da página), em ordem; vazio para DOCX/TXT.
    paginas: list[tuple[int, int]]

    def pagina_em(self, offset: int) -> int | None:
        pagina = None
        for inicio, numero in self.paginas:
            if inicio > offset:
                break
            pagina = numero
        return pagina

    def a_partir_de(self, offset: int) -> "_Segmento":
        paginas = [(inicio - offset, n) for inicio, n in self.paginas if inicio > offset]
        atual = self.pagina_em(offset)
        if atual is not None:
            paginas.insert(0, (0, atual))
        return _Segmento(self.texto[offset:], paginas)

    def seguido_de(self, outro: "_Segmento") -> "_Segmento":
        deslocamento = len(self.texto) + 1
        return _Segmento(
            f"{self.texto}\n{outro.texto}",
            self.paginas + [(inicio + deslocamento, n) for inicio, n in outro.paginas],
        )


def _abrir_pdf(document_bytes: bytes) -> pdfium.PdfDocument:
    try:
        pdf = pdfium.PdfDocument(document_bytes)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise DocumentoIlegivel(
                "O PDF está protegido por senha. Envie uma versão sem senha."
            ) from exc
        raise DocumentoIlegivel(
            "Não foi possível abrir o PDF: o arquivo parece corrompido."
        ) from exc
    total = len(pdf)
    if total > MAX_PDF_PAGES:
        pdf.close()
        raise DocumentoIlegivel(
            f"O PDF tem {total} páginas e o limite é {MAX_PDF_PAGES}. "
            "Divida o arquivo em partes menores."
        )
    return pdf


def _ler_paginas(pdf: pdfium.PdfDocument, inicio: int, fim: int) -> list[str]:
    textos = []
    for indice in range(inicio, fim):
        page = pdf[indice]
        try:
            textpage = page.get_textpage()
            try:
                textos.append(textpage.get_text_range())
            finally:
                textpage.close()
        finally:
            page.close()
    return textos


def _precisa_ocr(texto: str) -> bool:
    """Página sem texto (escaneada) ou com texto embaralhado (fonte sem mapa Unicode)."""
    visiveis = re.sub(r"\s", "", texto)
    if len(visiveis) < OCR_MIN_PAGE_CHARS:
        return True
    if len(_CONTROLE.findall(texto)) > len(visiveis) * 0.01:
        return True
    letras = [c for c in unicodedata.normalize("NFD", texto.lower()) if c.isalpha()]
    if len(letras) < OCR_MIN_PAGE_CHARS:
        return False  # página de números/símbolos: o texto nativo é o melhor que há
    vogais = sum(1 for c in letras if c in "aeiou")
    return vogais / len(letras) < 0.25


def _contar_paginas_para_ocr(pdf: pdfium.PdfDocument) -> int:
    return sum(1 for texto in _ler_paginas(pdf, 0, len(pdf)) if _precisa_ocr(texto))


def _renderizar_pagina(pdf: pdfium.PdfDocument, indice: int) -> bytes:
    """Imagem PNG em tons de cinza da página, no DPI do OCR (reduzido em páginas enormes)."""
    page = pdf[indice]
    try:
        largura, altura = page.get_size()
        escala = OCR_DPI / 72
        pixels = largura * altura * escala * escala
        if pixels > OCR_MAX_PIXELS:
            escala *= (OCR_MAX_PIXELS / pixels) ** 0.5
        bitmap = page.render(scale=escala, grayscale=True)
        try:
            buffer = io.BytesIO()
            dpi = round(escala * 72)
            bitmap.to_pil().save(buffer, format="PNG", dpi=(dpi, dpi))
            return buffer.getvalue()
        finally:
            bitmap.close()
    finally:
        page.close()


def _baixar_prioridade() -> None:
    try:
        os.nice(10)
    except OSError:
        pass


async def _tesseract(argumentos: list[str], imagem: bytes) -> str | None:
    """Roda o Tesseract na imagem (1 núcleo, prioridade baixa). None se falhar."""
    try:
        processo = await asyncio.create_subprocess_exec(
            "tesseract",
            "stdin",
            "stdout",
            *argumentos,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            preexec_fn=_baixar_prioridade,
        )
    except FileNotFoundError:
        logger.error("Tesseract não encontrado — OCR indisponível")
        return None
    try:
        saida, _ = await asyncio.wait_for(processo.communicate(imagem), OCR_PAGE_TIMEOUT_SECONDS)
    except TimeoutError:
        processo.kill()
        await processo.wait()
        logger.warning("OCR excedeu o tempo da página | limite={}s", OCR_PAGE_TIMEOUT_SECONDS)
        return None
    except asyncio.CancelledError:
        # Outra página do lote falhou: não deixa o Tesseract rodando sozinho.
        processo.kill()
        raise
    if processo.returncode != 0:
        # Detecção de orientação falha, por exemplo, em página com pouco texto.
        logger.debug("Tesseract terminou com erro | codigo={}", processo.returncode)
        return None
    return saida.decode("utf-8", errors="replace")


_ROTACAO = re.compile(r"^Rotate:\s*(\d+)", re.MULTILINE)
_PALAVRAS_COMUNS = frozenset(
    "a o e de do da dos das em no na nos nas que para por com não um uma os as ao se "
    "ou mais como sua seu ser foi pela pelo".split()
)


def _girar(imagem: bytes, graus: int) -> bytes:
    """Gira a imagem no sentido informado pelo Tesseract ("Rotate: N")."""
    from PIL import Image

    with Image.open(io.BytesIO(imagem)) as original:
        dpi = original.info.get("dpi")
        girada = original.rotate(-graus, expand=True)
    buffer = io.BytesIO()
    girada.save(buffer, format="PNG", **({"dpi": dpi} if dpi else {}))
    return buffer.getvalue()


def _pontuar(texto: str) -> int:
    """Quantas palavras comuns do português o texto tem — mede se a leitura faz sentido."""
    return sum(1 for palavra in re.findall(r"\w+", texto.lower()) if palavra in _PALAVRAS_COMUNS)


@dataclass
class _LeituraOcr:
    texto: str
    # Confiança média do Tesseract (0 a 100) nas palavras com letras; None sem palavras.
    confianca: float | None = None
    palavras: int = 0

    @property
    def fraca(self) -> bool:
        """Leitura de baixa qualidade (digitalização ruim, foto, página torta).

        Página com poucas palavras (capa, assinatura, carimbo) não é julgada.
        """
        return (
            self.confianca is not None
            and self.palavras >= OCR_MIN_WORDS_TO_JUDGE
            and self.confianca < OCR_MIN_CONFIDENCE
        )


def _ler_tsv(tsv: str) -> _LeituraOcr:
    """Texto (linhas e parágrafos preservados) e confiança média da saída TSV do Tesseract."""
    linhas: list[str] = []
    confiancas: list[float] = []
    atual, chave_linha, chave_paragrafo = [], None, None
    for registro in tsv.splitlines()[1:]:
        colunas = registro.split("\t")
        if len(colunas) < 12 or colunas[0] != "5":
            continue
        palavra = colunas[11].strip()
        if not palavra:
            continue
        paragrafo = tuple(colunas[1:4])
        linha = (*paragrafo, colunas[4])
        if linha != chave_linha:
            if atual:
                linhas.append(" ".join(atual))
            if chave_paragrafo is not None and paragrafo != chave_paragrafo:
                linhas.append("")
            atual, chave_linha, chave_paragrafo = [], linha, paragrafo
        atual.append(palavra)
        if any(c.isalpha() for c in palavra):
            try:
                confiancas.append(float(colunas[10]))
            except ValueError:
                pass
    if atual:
        linhas.append(" ".join(atual))
    media = sum(confiancas) / len(confiancas) if confiancas else None
    return _LeituraOcr("\n".join(linhas), media, len(confiancas))


def resumo_leitura(leitura: dict) -> dict:
    """Resumo público da leitura: páginas totais, lidas por OCR e as com leitura parcial."""
    parciais = sorted(set(leitura.get("sem_texto", [])) | set(leitura.get("fracas", [])))
    return {
        "paginas": leitura.get("paginas"),
        "ocr": leitura.get("ocr", 0),
        "paginas_parciais": parciais,
    }


def _melhor(a: _LeituraOcr, b: _LeituraOcr) -> _LeituraOcr:
    """A leitura que faz mais sentido em português; no empate, a de maior confiança."""
    chave_a = (_pontuar(a.texto), a.confianca or 0)
    chave_b = (_pontuar(b.texto), b.confianca or 0)
    return b if chave_b > chave_a else a


async def _ocr_imagem(imagem: bytes) -> _LeituraOcr:
    """Texto da imagem pelo Tesseract. Vazio se falhar.

    Lê direto: a maioria das páginas já está em pé e sai com confiança alta. Só uma
    leitura fraca ou vazia passa pela detecção de orientação; quando ela sugere girar,
    a página girada também é lida e fica a versão que faz mais sentido em português.
    """
    ler = ["-l", OCR_LANGUAGES, "--psm", "3", "tsv"]
    leitura = _ler_tsv(await _tesseract(ler, imagem) or "")
    if leitura.confianca is not None and leitura.confianca >= OCR_MIN_CONFIDENCE:
        return leitura
    orientacao = await _tesseract(["--psm", "0"], imagem)
    encontrada = _ROTACAO.search(orientacao or "")
    graus = int(encontrada.group(1)) % 360 if encontrada else 0
    if graus:
        girada = await asyncio.to_thread(_girar, imagem, graus)
        leitura = _melhor(leitura, _ler_tsv(await _tesseract(ler, girada) or ""))
    return leitura


async def _ocr_pagina(pdf: pdfium.PdfDocument, indice: int) -> _LeituraOcr:
    """Renderiza e lê uma página, ocupando uma vaga de OCR (no máximo uma imagem por vaga)."""
    loop = asyncio.get_running_loop()
    async with _ocr_slots:
        imagem = await loop.run_in_executor(_pdfium_thread, _renderizar_pagina, pdf, indice)
        return await _ocr_imagem(imagem)


def _decodificar_txt(document_bytes: bytes) -> str:
    try:
        return document_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return document_bytes.decode("latin-1")


def _fatiar_texto(texto: str) -> list[str]:
    """Divide um texto longo em lotes, cortando em quebra de linha quando possível."""
    lotes, inicio = [], 0
    while inicio < len(texto):
        fim = min(inicio + INGEST_TEXT_BATCH_CHARS, len(texto))
        if fim < len(texto):
            quebra = texto.rfind("\n", inicio, fim)
            if quebra > inicio:
                fim = quebra
        lotes.append(texto[inicio:fim])
        inicio = fim
    return lotes


# ---------- SERVICE ----------


class DocumentoService:
    def __init__(self, qdrant: QdrantClient, repo: DocumentoRepository):
        self.repo = repo
        self.qdrant = qdrant
        self.emb_model = _emb_model
        self.chunker = _chunker

    async def _segmentos(
        self, document_bytes: bytes, extensao: str, leitura: dict | None = None
    ) -> AsyncIterator[_Segmento]:
        """Entrega o documento em lotes, sem nunca carregar o texto inteiro de um PDF.

        Páginas de PDF sem texto ou com texto embaralhado passam pelo OCR; `leitura`
        recebe as contagens (páginas, lidas por OCR, sem texto mesmo com OCR) e os
        números das páginas sem texto (`sem_texto`) ou com leitura fraca (`fracas`).
        """
        leitura = leitura if leitura is not None else {}
        logger.debug(f"Extraindo texto | extensao={extensao}")
        if extensao == "pdf":
            loop = asyncio.get_running_loop()
            pdf = await loop.run_in_executor(_pdfium_thread, _abrir_pdf, document_bytes)
            try:
                total = len(pdf)
                leitura.update(paginas=total, ocr=0, ilegiveis=0, sem_texto=[], fracas=[])
                if OCR_ENABLED:
                    escaneadas = await loop.run_in_executor(
                        _pdfium_thread, _contar_paginas_para_ocr, pdf
                    )
                    if escaneadas > MAX_OCR_PAGES:
                        raise DocumentoIlegivel(
                            f"O PDF tem {escaneadas} páginas escaneadas e o limite de leitura "
                            f"por OCR é {MAX_OCR_PAGES}. Divida o arquivo em partes menores."
                        )
                for inicio in range(0, total, INGEST_PAGE_BATCH):
                    fim = min(inicio + INGEST_PAGE_BATCH, total)
                    textos = await loop.run_in_executor(
                        _pdfium_thread, _ler_paginas, pdf, inicio, fim
                    )
                    if OCR_ENABLED:
                        # Páginas escaneadas do lote lidas em paralelo (limite: _ocr_slots);
                        # cada resultado volta para a posição da sua página. Se uma falhar,
                        # o TaskGroup cancela as outras antes de o PDF ser fechado.
                        escaneadas = [p for p, t in enumerate(textos) if _precisa_ocr(t)]
                        async with asyncio.TaskGroup() as grupo:
                            tarefas = [
                                grupo.create_task(_ocr_pagina(pdf, inicio + p)) for p in escaneadas
                            ]
                        for posicao, tarefa in zip(escaneadas, tarefas, strict=True):
                            texto, lido = textos[posicao], tarefa.result()
                            leitura["ocr"] += 1
                            if lido.texto.strip():
                                textos[posicao] = lido.texto
                                if lido.fraca:
                                    leitura["fracas"].append(inicio + posicao + 1)
                                continue
                            leitura["ilegiveis"] += 1
                            leitura["sem_texto"].append(inicio + posicao + 1)
                            # Sem leitura do OCR, fica o pouco texto nativo que houver; o
                            # embaralhado (fonte sem mapa Unicode) só atrapalharia a busca.
                            if len(re.sub(r"\s", "", texto)) >= OCR_MIN_PAGE_CHARS:
                                textos[posicao] = ""
                    paginas, offset = [], 0
                    for numero, texto in enumerate(textos, start=inicio + 1):
                        paginas.append((offset, numero))
                        offset += len(texto) + 1
                    yield _Segmento("\n".join(textos), paginas)
            finally:
                await loop.run_in_executor(_pdfium_thread, pdf.close)
        elif extensao == "docx":
            paragrafos = Document(io.BytesIO(document_bytes)).paragraphs
            for lote in _fatiar_texto("\n".join(p.text for p in paragrafos)):
                yield _Segmento(lote, [])
        elif extensao == "txt":
            for lote in _fatiar_texto(_decodificar_txt(document_bytes)):
                yield _Segmento(lote, [])
        else:
            raise ValueError(f"Formato não suportado: {extensao}")

    async def get_sparse_embeddings_batch(self, texts: list[str], document_id: str) -> list[dict]:
        vetores: list[dict] = []
        async with httpx.AsyncClient(timeout=60) as client:
            for inicio in range(0, len(texts), SPARSE_BATCH):
                response = await client.post(
                    URL_API_LOCAL_SPARSE,
                    json={
                        "document_id": document_id,
                        "texts": texts[inicio : inicio + SPARSE_BATCH],
                    },
                )
                response.raise_for_status()
                vetores.extend(response.json()["vectors"])
        return vetores

    async def _gravar_trechos(self, trechos: list[tuple], document_id: str, payload: dict) -> None:
        """Vetoriza e grava um lote de trechos (texto, página inicial, página final)."""
        textos = [texto for texto, _, _ in trechos]
        embeddings = await self.emb_model.aembed_documents(textos)
        sparse = await self.get_sparse_embeddings_batch(textos, document_id)
        paginas = [(inicio, fim) for _, inicio, fim in trechos]
        await self._salvar_qdrant(textos, embeddings, sparse, payload, paginas)

    async def _indexar(
        self,
        document_bytes: bytes,
        extensao: str,
        document_id: str,
        payload: dict,
        leitura: dict | None = None,
    ) -> int:
        """Lê, divide, vetoriza e grava um lote por vez. Devolve quantos trechos gravou.

        O último trecho de cada lote pode ter sido cortado na emenda: ele volta para
        o começo do lote seguinte, para nenhuma frase ficar partida entre trechos.
        `leitura` recebe o resultado da leitura (ver `_segmentos`).
        """
        total = 0
        sobra: _Segmento | None = None
        leitura = leitura if leitura is not None else {}
        async with aclosing(self._segmentos(document_bytes, extensao, leitura)) as segmentos:
            async for segmento in segmentos:
                if sobra is not None:
                    segmento = sobra.seguido_de(segmento)
                chunks = await asyncio.to_thread(self.chunker, segmento.texto)
                if not chunks:
                    sobra = segmento
                    continue
                sobra = segmento.a_partir_de(chunks[-1].start_index)
                total += await self._gravar_chunks(segmento, chunks[:-1], document_id, payload)
        if sobra is not None:
            chunks = await asyncio.to_thread(self.chunker, sobra.texto)
            total += await self._gravar_chunks(sobra, chunks, document_id, payload)
        if leitura.get("ocr"):
            logger.info(
                "Leitura por OCR | paginas={} | ocr={} | sem_texto_mesmo_com_ocr={} | "
                "leitura_fraca={}",
                leitura["paginas"],
                leitura["ocr"],
                leitura["ilegiveis"],
                len(leitura["fracas"]),
            )
        if total == 0:
            if leitura.get("ocr"):
                raise DocumentoIlegivel(
                    "Não foi possível ler texto no arquivo, nem com OCR. "
                    "Verifique a qualidade da digitalização."
                )
            raise DocumentoIlegivel(
                "Não há texto legível no arquivo."
                if OCR_ENABLED
                else "Não há texto legível no arquivo. Se for um PDF escaneado, "
                "a leitura por OCR está desativada."
            )
        resumo = resumo_leitura(leitura)
        if resumo["paginas_parciais"]:
            # Todo trecho do documento carrega as páginas com leitura parcial: o agente
            # avisa da limitação mesmo quando a busca traz um trecho de página boa.
            result = await self.qdrant.set_payload_by_filter(
                collection_name=QDRANT_COLLECTION,
                tenant_id=payload["tenant_id"],
                field="doc_id",
                value=payload["doc_id"],
                payload={
                    "partial_pages": resumo["paginas_parciais"],
                    "pages_total": resumo["paginas"],
                },
            )
            if not result.get("success"):
                error = result.get("error") or "erro desconhecido"
                raise RuntimeError(f"Falha ao marcar leitura parcial no Qdrant: {error}")
        return total

    async def _gravar_chunks(self, segmento: _Segmento, chunks, document_id, payload) -> int:
        trechos = [
            (
                chunk.text,
                segmento.pagina_em(chunk.start_index),
                segmento.pagina_em(max(chunk.start_index, chunk.end_index - 1)),
            )
            for chunk in chunks
            if chunk.text.strip()
        ]
        if trechos:
            await self._gravar_trechos(trechos, document_id, payload)
        return len(trechos)

    def _salvar_arquivo(self, base_dir: str, path_doc: str, file_name: str, document_bytes: bytes):
        os.makedirs(f"{base_dir}/{path_doc}", exist_ok=True)
        with open(f"{base_dir}/{path_doc}/{file_name}", "wb") as f:
            f.write(document_bytes)
        logger.debug("Arquivo salvo | bytes={}", len(document_bytes))
        return base_dir, path_doc

    async def _salvar_qdrant(
        self, texts_list, embeddings_list, sparse_list, payload: dict, paginas=None
    ):
        """Upsert dos chunks na collection única, em lotes.

        O payload precisa vir com tenant_id (validado também no client);
        o texto do chunk vai na chave `text` — a mesma lida pelo retrieval.
        """
        if not payload.get("tenant_id"):
            raise ValueError("payload sem tenant_id — ingestão abortada")

        points = []
        for i, text in enumerate(texts_list):
            sv = sparse_list[i]
            ponto_payload = {**payload, "text": text}
            if paginas and paginas[i][0] is not None:
                ponto_payload["page_start"], ponto_payload["page_end"] = paginas[i]
            points.append(
                PointStruct(
                    id=str(uuid4()),
                    vector={
                        "dense": embeddings_list[i],
                        "sparse": SparseVector(indices=sv["indices"], values=sv["values"]),
                    },
                    payload=ponto_payload,
                )
            )
        for inicio in range(0, len(points), QDRANT_UPSERT_BATCH):
            result = await self.qdrant.upsert_points(
                collection_name=QDRANT_COLLECTION,
                points=points[inicio : inicio + QDRANT_UPSERT_BATCH],
            )
            if not result.get("success"):
                error = result.get("error") or "erro desconhecido"
                raise RuntimeError(f"Falha ao indexar documento no Qdrant: {error}")
        logger.debug(f"Qdrant upsert | collection={QDRANT_COLLECTION} | pontos={len(points)}")

    @staticmethod
    def _extensao(file_name: str) -> str:
        extensao = file_name.split(".")[-1].lower()
        if extensao not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Formato não suportado: {extensao}")
        return extensao

    # ──  Documentos do Usuário ──────────────────────────────────────────

    async def inserir_documento_usuario(
        self,
        files: list[UploadFile],
        tenant_id: str,
        conversation_id: str,
        doc_id: str | None = None,
    ):
        if not tenant_id:
            raise ValueError("tenant_id é obrigatório")

        logger.info("Processando para bytes ...")
        list_documents_bytes = [
            {"file_name": file.filename, "file_bytes": await file.read()} for file in files
        ]

        resumos: list[dict] = []
        async with _ingest_slots:
            for doc_item in list_documents_bytes:
                extensao = self._extensao(doc_item["file_name"])
                if doc_id is not None:
                    # Re-ingestão idempotente: retry do worker com o mesmo doc_id
                    # substitui o documento anterior (disco + Qdrant + Postgres).
                    # Deletar ANTES de gravar o arquivo novo — o path do antigo é
                    # o mesmo (tenant + conversation + filename) e a deleção
                    # apagaria o arquivo recém-gravado.
                    existente = await self.repo.buscar_documento_usuario_por_id(UUID(doc_id))
                    if existente is not None:
                        await self.deletar_documento_usuario(tenant_id, [doc_id])
                # Disco escopado por tenant: {UPLOAD_DIR_USER}/{tenant_id}/{conversation_id}/
                path_base, path_doc = self._salvar_arquivo(
                    UPLOAD_DIR_USER,
                    f"{tenant_id}/{conversation_id}",
                    doc_item["file_name"],
                    doc_item["file_bytes"],
                )

                instance = DocumentoUsuario(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    nome=doc_item["file_name"],
                    extensao=extensao,
                    path_base=path_base,
                    path_doc=path_doc,
                )
                if doc_id is not None:
                    instance.id = UUID(doc_id)
                doc = await self.repo.criar_documento_usuario(instance)
                logger.info(f"Documento salvo no banco | id={doc.id}")

                leitura: dict = {}
                try:
                    trechos = await self._indexar(
                        doc_item["file_bytes"],
                        extensao,
                        conversation_id,
                        {
                            "tenant_id": tenant_id,
                            "conversation_id": conversation_id,
                            "name": doc_item["file_name"],
                            "doc_id": str(doc.id),
                        },
                        leitura,
                    )
                except Exception:
                    # Nada fica pela metade: lotes já gravados, arquivo e registro saem.
                    await self.deletar_documento_usuario(tenant_id, [str(doc.id)])
                    raise
                logger.info(
                    "Documento inserido | doc_id={} | trechos={} | tenant_ref={} | "
                    "conversation_ref={}",
                    doc.id,
                    trechos,
                    safe_identifier(tenant_id),
                    safe_identifier(conversation_id),
                )
                resumos.append(resumo_leitura(leitura))

        logger.info("Processamento concluido")
        return resumos

    async def deletar_documento_usuario(self, tenant_id: str, docs_ids: list[str]):
        if not tenant_id:
            raise ValueError("tenant_id é obrigatório")

        logger.info(
            "Deletando documentos | total={} | tenant_ref={}",
            len(docs_ids),
            safe_identifier(tenant_id),
        )

        for doc_id in docs_ids:
            doc = await self.repo.buscar_documento_usuario_por_id(UUID(doc_id))
            if doc is None or doc.tenant_id != tenant_id:
                # Documento inexistente ou de outro tenant — nunca vaza nem deleta.
                logger.warning(
                    f"Documento não encontrado para o tenant | doc_id={doc_id} | tenant={tenant_id}"
                )
                continue

            caminho = f"{doc.path_base}/{doc.path_doc}/{doc.nome}"
            if os.path.exists(caminho):
                os.remove(caminho)
                logger.debug("Arquivo de documento removido | doc_id={}", doc_id)

            await self.qdrant.delete_points_by_filter(
                collection_name=QDRANT_COLLECTION,
                tenant_id=tenant_id,
                field="doc_id",
                value=str(doc.id),
            )
            logger.debug(f"Pontos removidos do Qdrant | doc_id={doc_id}")

            await self.repo.deletar_documento_usuario(doc.id)
            logger.info(f"Documento {doc_id} deletado com sucesso")

    # ──  Documentos Sistema (base da plataforma, tenant reservado) ──────

    async def inserir_documento_sistema(self, files: list[UploadFile], base: str, id_drive: str):
        logger.info("Processando para bytes ...")
        list_documents_bytes = [
            {"file_name": file.filename, "file_bytes": await file.read()} for file in files
        ]

        async with _ingest_slots:
            for doc_item in list_documents_bytes:
                extensao = self._extensao(doc_item["file_name"])
                path_base, path_doc = self._salvar_arquivo(
                    UPLOAD_DIR_SYSTEM, base, doc_item["file_name"], doc_item["file_bytes"]
                )

                instance = DocumentoSistema(
                    base=base,
                    nome=doc_item["file_name"],
                    id_drive=id_drive,
                    extensao=extensao,
                    path_base=path_base,
                    path_doc=path_doc,
                )
                doc = await self.repo.criar_documento_sistema(instance)
                logger.info(f"Documento salvo no banco | id={doc.id}")

                try:
                    trechos = await self._indexar(
                        doc_item["file_bytes"],
                        extensao,
                        base,
                        {
                            "tenant_id": SYSTEM_TENANT_ID,
                            "base": base,
                            "name": doc_item["file_name"],
                            "doc_id": str(doc.id),
                            "id_drive": id_drive,
                        },
                    )
                except Exception:
                    await self.deletar_documento_sistema([str(doc.id)])
                    raise
                logger.info(
                    "Documento inserido com sucesso | doc_id={} | trechos={} | base={}",
                    doc.id,
                    trechos,
                    base,
                )

        logger.info("Processamento concluido")
        return

    async def deletar_documento_sistema(self, docs_ids: list[str]):
        logger.info(f"Deletando {len(docs_ids)} documentos do sistema...")

        for doc_id in docs_ids:
            doc = await self.repo.buscar_documento_sistema_por_id(UUID(doc_id))
            if doc is None:
                logger.warning(f"Documento não encontrado | doc_id={doc_id}")
                continue

            caminho = f"{doc.path_base}/{doc.path_doc}/{doc.nome}"
            if os.path.exists(caminho):
                os.remove(caminho)
                logger.debug("Arquivo de documento removido | doc_id={}", doc_id)

            await self.qdrant.delete_points_by_filter(
                collection_name=QDRANT_COLLECTION,
                tenant_id=SYSTEM_TENANT_ID,
                field="doc_id",
                value=str(doc.id),
            )
            logger.debug(f"Pontos removidos do Qdrant | doc_id={doc_id}")

            await self.repo.deletar_documento_sistema(doc.id)
            logger.info(f"Documento {doc_id} deletado com sucesso")
