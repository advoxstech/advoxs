import asyncio
import io
import os
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
    MAX_PDF_PAGES,
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

    async def _segmentos(self, document_bytes: bytes, extensao: str) -> AsyncIterator[_Segmento]:
        """Entrega o documento em lotes, sem nunca carregar o texto inteiro de um PDF."""
        logger.debug(f"Extraindo texto | extensao={extensao}")
        if extensao == "pdf":
            loop = asyncio.get_running_loop()
            pdf = await loop.run_in_executor(_pdfium_thread, _abrir_pdf, document_bytes)
            try:
                total = len(pdf)
                for inicio in range(0, total, INGEST_PAGE_BATCH):
                    fim = min(inicio + INGEST_PAGE_BATCH, total)
                    textos = await loop.run_in_executor(
                        _pdfium_thread, _ler_paginas, pdf, inicio, fim
                    )
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
        self, document_bytes: bytes, extensao: str, document_id: str, payload: dict
    ) -> int:
        """Lê, divide, vetoriza e grava um lote por vez. Devolve quantos trechos gravou.

        O último trecho de cada lote pode ter sido cortado na emenda: ele volta para
        o começo do lote seguinte, para nenhuma frase ficar partida entre trechos.
        """
        total = 0
        sobra: _Segmento | None = None
        async with aclosing(self._segmentos(document_bytes, extensao)) as segmentos:
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
        if total == 0:
            raise DocumentoIlegivel(
                "Não há texto legível no arquivo. Se for um PDF escaneado, "
                "a leitura por OCR ainda não é suportada."
            )
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

        logger.info("Processamento concluido")
        return

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
