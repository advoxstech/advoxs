import io
import os
from uuid import UUID, uuid4

import httpx
import pdfplumber
from chonkie import RecursiveChunker
from docx import Document
from dotenv import load_dotenv
from fastapi import UploadFile
from langchain_openai import OpenAIEmbeddings
from loguru import logger
from qdrant_client.models import PointStruct, SparseVector

from clients.qdrant import QdrantClient
from constants import QDRANT_COLLECTION, SYSTEM_TENANT_ID
from database.models import DocumentoSistema, DocumentoUsuario
from database.repositories.documento import DocumentoRepository

load_dotenv()

# ---------- CONFIG ----------

DENSE_MODEL = os.getenv("DENSE_MODEL")
URL_API_LOCAL_SPARSE = os.getenv("URL_API_LOCAL_SPARSE")
UPLOAD_DIR_USER = os.getenv("UPLOAD_DIR_USER")
UPLOAD_DIR_SYSTEM = os.getenv("UPLOAD_DIR_SYSTEM")

# ---------- MODELOS (carregados uma vez) ----------

_emb_model = OpenAIEmbeddings(model=DENSE_MODEL)
_chunker = RecursiveChunker()


# ---------- SERVICE ----------


class DocumentoService:
    def __init__(self, qdrant: QdrantClient, repo: DocumentoRepository):
        self.repo = repo
        self.qdrant = qdrant
        self.emb_model = _emb_model
        self.chunker = _chunker

    def _extrair_texto(self, document_bytes: bytes, extensao: str) -> str:
        logger.debug(f"Extraindo texto | extensao={extensao}")
        if extensao == "pdf":
            with pdfplumber.open(io.BytesIO(document_bytes)) as pdf:
                return "\n".join([p.extract_text() or "" for p in pdf.pages])
        elif extensao == "docx":
            doc = Document(io.BytesIO(document_bytes))
            return "\n".join([p.text for p in doc.paragraphs])
        else:
            raise ValueError(f"Formato não suportado: {extensao}")

    async def get_sparse_embeddings_batch(self, texts: list[str], document_id: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                URL_API_LOCAL_SPARSE, json={"document_id": document_id, "texts": texts}
            )
            response.raise_for_status()
            return response.json()["vectors"]

    async def _processar_documento(self, document_bytes: bytes, file_name: str, document_id: str):
        extensao = file_name.split(".")[-1].lower()
        texto = self._extrair_texto(document_bytes, extensao)

        chunks = self.chunker(texto)
        texts_list = [chunk.text for chunk in chunks]
        logger.debug(f"Documento '{file_name}' dividido em {len(texts_list)} chunks")

        embeddings_list = await self.emb_model.aembed_documents(texts_list)
        logger.debug(f"Embeddings gerados | total={len(embeddings_list)}")

        sparse_list = await self.get_sparse_embeddings_batch(texts_list, document_id)
        logger.debug(f"Vetores sparse gerados | total={len(sparse_list)}")

        return extensao, texto, texts_list, embeddings_list, sparse_list

    def _salvar_arquivo(self, base_dir: str, path_doc: str, file_name: str, document_bytes: bytes):
        os.makedirs(f"{base_dir}/{path_doc}", exist_ok=True)
        with open(f"{base_dir}/{path_doc}/{file_name}", "wb") as f:
            f.write(document_bytes)
        logger.debug(f"Arquivo salvo em {base_dir}/{path_doc}/{file_name}")
        return base_dir, path_doc

    async def _salvar_qdrant(self, texts_list, embeddings_list, sparse_list, payload: dict):
        """Upsert dos chunks na collection única.

        O payload precisa vir com tenant_id (validado também no client);
        o texto do chunk vai na chave `text` — a mesma lida pelo retrieval.
        """
        if not payload.get("tenant_id"):
            raise ValueError("payload sem tenant_id — ingestão abortada")

        points = []
        for i, text in enumerate(texts_list):
            sv = sparse_list[i]
            points.append(
                PointStruct(
                    id=str(uuid4()),
                    vector={
                        "dense": embeddings_list[i],
                        "sparse": SparseVector(indices=sv["indices"], values=sv["values"]),
                    },
                    payload={**payload, "text": text},
                )
            )
        await self.qdrant.upsert_points(collection_name=QDRANT_COLLECTION, points=points)
        logger.debug(f"Qdrant upsert | collection={QDRANT_COLLECTION} | pontos={len(points)}")

    # ──  Documentos do Usuário ──────────────────────────────────────────

    async def inserir_documento_usuario(
        self, files: list[UploadFile], tenant_id: str, conversation_id: str
    ):
        if not tenant_id:
            raise ValueError("tenant_id é obrigatório")

        logger.info("Processando para bytes ...")
        list_documents_bytes = [
            {"file_name": file.filename, "file_bytes": await file.read()} for file in files
        ]

        for doc_item in list_documents_bytes:
            (
                extensao,
                texto,
                texts_list,
                embeddings_list,
                sparse_list,
            ) = await self._processar_documento(
                doc_item["file_bytes"], doc_item["file_name"], conversation_id
            )
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
            doc = await self.repo.criar_documento_usuario(instance)
            logger.info(f"Documento salvo no banco | id={doc.id}")

            await self._salvar_qdrant(
                texts_list,
                embeddings_list,
                sparse_list,
                {
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                    "name": doc_item["file_name"],
                    "doc_id": str(doc.id),
                },
            )
            logger.info(
                f"Documento '{doc_item['file_name']}' inserido | tenant={tenant_id}"
                f" | conversa={conversation_id}"
            )

        logger.info("Processamento concluido")
        return

    async def deletar_documento_usuario(self, tenant_id: str, docs_ids: list[str]):
        if not tenant_id:
            raise ValueError("tenant_id é obrigatório")

        logger.info(f"Deletando {len(docs_ids)} documentos | tenant={tenant_id}")

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
                logger.debug(f"Arquivo removido | caminho={caminho}")

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

        for doc_item in list_documents_bytes:
            (
                extensao,
                texto,
                texts_list,
                embeddings_list,
                sparse_list,
            ) = await self._processar_documento(doc_item["file_bytes"], doc_item["file_name"], base)
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

            await self._salvar_qdrant(
                texts_list,
                embeddings_list,
                sparse_list,
                {
                    "tenant_id": SYSTEM_TENANT_ID,
                    "base": base,
                    "name": doc_item["file_name"],
                    "doc_id": str(doc.id),
                    "id_drive": id_drive,
                },
            )
            logger.info(f"Documento '{doc_item['file_name']}' inserido com sucesso | base={base}")

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
                logger.debug(f"Arquivo removido | caminho={caminho}")

            await self.qdrant.delete_points_by_filter(
                collection_name=QDRANT_COLLECTION,
                tenant_id=SYSTEM_TENANT_ID,
                field="doc_id",
                value=str(doc.id),
            )
            logger.debug(f"Pontos removidos do Qdrant | doc_id={doc_id}")

            await self.repo.deletar_documento_sistema(doc.id)
            logger.info(f"Documento {doc_id} deletado com sucesso")
