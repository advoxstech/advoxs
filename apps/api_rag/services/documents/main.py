from langchain_openai import OpenAIEmbeddings
from fastapi import UploadFile
from qdrant_client.models import PointStruct, SparseVector
from chonkie import RecursiveChunker
from sqlalchemy.ext.asyncio import AsyncSession
from clients.qdrant import QdrantClient
from database.models import DocumentoUsuario, DocumentoSistema
from database.repositories.documento import DocumentoRepository
from qdrant_client.models import Filter, FieldCondition, MatchValue, FilterSelector
from loguru import logger
import requests
import pdfplumber
from docx import Document
import io
from dotenv import load_dotenv
from uuid import uuid4
import os 

load_dotenv()

# ---------- CONFIG ----------

COLLECTION_SISTEMA = os.getenv("COLLECTION_SISTEMA")
COLLECTION_USERS = os.getenv("COLLECTION_USERS")
DENSE_MODEL  = os.getenv("DENSE_MODEL")
URL_API_LOCAL_SPARSE = os.getenv("URL_API_LOCAL_SPARSE")
UPLOAD_DIR_USER = os.getenv("UPLOAD_DIR_USER")
UPLOAD_DIR_SYSTEM = os.getenv("UPLOAD_DIR_SYSTEM")

# ---------- MODELOS (carregados uma vez) ----------

_emb_model    = OpenAIEmbeddings(model=DENSE_MODEL)
_chunker      = RecursiveChunker()


# ---------- SERVICE ----------

class DocumentoService:
    def __init__(self, qdrant: QdrantClient, repo: DocumentoRepository):
        self.repo         = repo
        self.qdrant       = qdrant
        self.emb_model    = _emb_model
        self.chunker      = _chunker

    def _extrair_texto(self, document_bytes: bytes, extensao: str) -> str:
        logger.debug(f"Extraindo texto | extensao={extensao}")
        if extensao == "pdf":
            with pdfplumber.open(io.BytesIO(document_bytes)) as pdf:
                return "\n".join([p.extract_text() for p in pdf.pages])
        elif extensao == "docx":
            doc = Document(io.BytesIO(document_bytes))
            return "\n".join([p.text for p in doc.paragraphs])
        else:
            raise ValueError(f"Formato não suportado: {extensao}")

    async def get_sparse_embeddings_batch(self, texts: list[str], usuario_id: str) -> list[dict]:
        response = requests.post(URL_API_LOCAL_SPARSE, json={"document_id": usuario_id, "texts": texts})
        data = response.json()
        return data["vectors"]  
    

    async def _processar_documento(self, document_bytes: bytes, file_name: str, usuario_id: str):
        extensao = file_name.split(".")[-1].lower()
        texto = self._extrair_texto(document_bytes, extensao)

        chunks = self.chunker(texto)
        texts_list = [chunk.text for chunk in chunks]
        logger.debug(f"Documento '{file_name}' dividido em {len(texts_list)} chunks")

        embeddings_list = await self.emb_model.aembed_documents(texts_list)
        logger.debug(f"Embeddings gerados | total={len(embeddings_list)}")

        sparse_list = await self.get_sparse_embeddings_batch(texts_list, usuario_id)
        logger.debug(f"Vetores sparse gerados | total={len(sparse_list)}")

        return extensao, texto, texts_list, embeddings_list, sparse_list

    def _salvar_arquivo_usuario(self, document_bytes: bytes, file_name: str, usuario_id: str) -> tuple[str, str]:
        path_doc = f"{usuario_id}"
        os.makedirs(f"{UPLOAD_DIR_USER}/{path_doc}", exist_ok=True)
        with open(f"{UPLOAD_DIR_USER}/{path_doc}/{file_name}", "wb") as f:
            f.write(document_bytes)
        logger.debug(f"Arquivo salvo em {UPLOAD_DIR_USER}/{path_doc}/{file_name}")
        return UPLOAD_DIR_USER, path_doc

    def _salvar_arquivo_sistema(self, document_bytes: bytes, file_name: str, base: str) -> tuple[str, str]:
        path_doc = f"{base}"
        os.makedirs(f"{UPLOAD_DIR_SYSTEM}/{path_doc}", exist_ok=True)
        with open(f"{UPLOAD_DIR_SYSTEM}/{path_doc}/{file_name}", "wb") as f:
            f.write(document_bytes)
        logger.debug(f"Arquivo salvo em {UPLOAD_DIR_SYSTEM}/{path_doc}/{file_name}")
        return UPLOAD_DIR_SYSTEM, path_doc
    async def _salvar_qdrant(self, collection: str, texts_list, embeddings_list, sparse_list, payload: dict):
        points = []
        for i, text in enumerate(texts_list):
            sv = sparse_list[i]
            points.append(PointStruct(
                id=str(uuid4()),
                vector={
                    "dense": embeddings_list[i],
                    "sparse": SparseVector(
                        indices=sv["indices"],
                        values=sv["values"]
                    )
                },
                payload={**payload, "texto": text}
            ))
        await self.qdrant.upsert_points(collection_name=collection, points=points)
        logger.debug(f"Qdrant upsert | collection={collection} | pontos={len(points)}")

  
    async def baixar_e_inserir_pdfs(self, pdf_urls: list[str], conversation_id: str) -> list[str]:
        import httpx

        logger.info(f"Iniciando download de {len(pdf_urls)} PDFs | conversa={conversation_id}")

        nomes_inseridos = []

        async with httpx.AsyncClient() as client:
            for url in pdf_urls:
                file_name = url.split("/")[-1].split("?")[0]  # extrai nome da URL
                if not file_name.endswith(".pdf"):
                    file_name += ".pdf"

                try:
                    logger.info(f"Baixando PDF: {url}")
                    response = await client.get(url, follow_redirects=True)
                    response.raise_for_status()

                    document_bytes = response.content
                    await self.inserir_documento_usuario(document_bytes, file_name, conversation_id)
                    nomes_inseridos.append(file_name)
                    logger.info(f"PDF '{file_name}' inserido com sucesso")

                except Exception as e:
                    logger.error(f"Erro ao processar PDF '{url}': {e}")
                    # continua tentando os demais

        logger.info(f"{len(nomes_inseridos)}/{len(pdf_urls)} PDFs inseridos | usuario={conversation_id}")
        return nomes_inseridos
 



  # ──  Documentos do Usuário ──────────────────────────────────────────

    async def inserir_documento_usuario(self, files: list[UploadFile], conversation_id: str):
        logger.info(f"Processando para bytes ...")
        list_documents_bytes = [{"file_name": file.filename, "file_bytes": await file.read()} for file in files]

        for doc_item in list_documents_bytes:

            extensao, texto, texts_list, embeddings_list, sparse_list = await self._processar_documento(doc_item["file_bytes"], doc_item["file_name"], conversation_id)
            path_base, path_doc = self._salvar_arquivo_usuario(doc_item["file_bytes"], doc_item["file_name"], conversation_id)

            instance = DocumentoUsuario(
                conversation_id=conversation_id,
                nome=doc_item["file_name"],
                extensao=extensao,
                path_base=path_base,
                path_doc=path_doc,
            )
            doc = await self.repo.criar_documento_usuario(instance)
            logger.info(f"Documento salvo no banco | id={doc.id}")

            await self._salvar_qdrant(COLLECTION_USERS, texts_list, embeddings_list, sparse_list, {"conversation_id": conversation_id, "name": doc_item["file_name"], "doc_id": doc.id})
            logger.info(f"Documento '{doc_item['file_name']}' inserido com sucesso | usuario={conversation_id}")
        
        logger.info(f"Processamento concluido")
        return 

    async def deletar_documento_usuario(self, docs_ids: list[str]):
        logger.info(f"Deletando {len(docs_ids)} documentos...")

        for doc_id in docs_ids:
            doc = await self.repo.buscar_documento_por_id(doc_id)
            if not doc or str(doc.doc_id) != str(doc_id):
                logger.warning(f"Documento não encontrado | doc_id={doc_id}")
                continue

            caminho = f"{doc.path_base}/{doc.path_doc}/{doc.nome}"
            if os.path.exists(caminho):
                os.remove(caminho)
                logger.debug(f"Arquivo removido | caminho={caminho}")

            await self.qdrant.delete_points_by_filter(
                collection_name=COLLECTION_USERS,
                fild="doc_id",
                value=str(doc_id)
                )

            logger.debug(f"Pontos removidos do Qdrant | doc_id={doc_id}")

            await self.repo.deletar_documento(doc_id)
            logger.info(f"Documento {doc_id} deletado com sucesso")



 # ──  Documentos Sistema ──────────────────────────────────────────

    async def inserir_documento_sistema(self, files: list[UploadFile], base: str, id_drive: str):
        logger.info(f"Processando para bytes ...")
        list_documents_bytes = [{"file_name": file.filename, "file_bytes": await file.read()} for file in files]

        for doc_item in list_documents_bytes:

            extensao, texto, texts_list, embeddings_list, sparse_list = await self._processar_documento(doc_item["file_bytes"], doc_item["file_name"], base)
            path_base, path_doc = self._salvar_arquivo_sistema(doc_item["file_bytes"], doc_item["file_name"], base)

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

            await self._salvar_qdrant(COLLECTION_SISTEMA, texts_list, embeddings_list, sparse_list, {"base": base, "name": doc_item["file_name"], "doc_id": doc.id, "id_drive": id_drive})
            logger.info(f"Documento '{doc_item['file_name']}' inserido com sucesso | base={base}")
        
        logger.info(f"Processamento concluido")
        return 

    async def deletar_documento_sistema(self, docs_ids: list[str]):
        
        logger.info(f"Deletando {len(docs_ids)} documentos...")

        for doc_id in docs_ids:
            doc = await self.repo.buscar_documento_por_id(doc_id)
            if not doc or str(doc.doc_id) != str(doc_id):
                logger.warning(f"Documento não encontrado | doc_id={doc_id}")
                continue

            caminho = f"{doc.path_base}/{doc.path_doc}/{doc.nome}"
            if os.path.exists(caminho):
                os.remove(caminho)
                logger.debug(f"Arquivo removido | caminho={caminho}")

            await self.qdrant.delete_points_by_filter(
                collection_name=COLLECTION_SISTEMA,
                fild="doc_id",
                value=str(doc_id)
                )

            logger.debug(f"Pontos removidos do Qdrant | doc_id={doc_id}")

            await self.repo.deletar_documento(doc_id)
            logger.info(f"Documento {doc_id} deletado com sucesso")