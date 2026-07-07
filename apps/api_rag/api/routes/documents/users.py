from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api.security import verify_api_key
from clients.qdrant import QdrantClient
from database.repositories.documento import DocumentoRepository
from database.session import get_session
from services.documents.main import DocumentoService

router_doc_users = APIRouter(prefix="/documents/users")


def get_qdrant():
    return QdrantClient()


async def get_repo(session: AsyncSession = Depends(get_session)):
    return DocumentoRepository(session=session)


async def get_service(
    repo: DocumentoRepository = Depends(get_repo),
    qdrant: QdrantClient = Depends(get_qdrant),
) -> DocumentoService:
    return DocumentoService(repo=repo, qdrant=qdrant)


# ── Documentos do Usuário (contato de um escritório) ────────────────────


@router_doc_users.post("/insert")
async def inserir_documento(
    tenant_id: str = Form(...),
    conversation_id: str = Form(...),
    file: UploadFile = File(...),
    service: DocumentoService = Depends(get_service),
    security: str = Depends(verify_api_key),
):
    try:
        files = [file]
        logger.info(f"Recebendo {len(files)} arquivos | tenant={tenant_id}")
        await service.inserir_documento_usuario(files, tenant_id, conversation_id)
        return {"mensagem": "Documentos inseridos com sucesso"}
    except ValueError as e:
        logger.warning(f"Erro de validação ao inserir documento: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Erro ao inserir documento: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router_doc_users.delete("/delete")
async def deletar_documentos(
    tenant_id: str = Query(...),
    docs_ids: list[str] = Query(...),
    service: DocumentoService = Depends(get_service),
    security: str = Depends(verify_api_key),
):
    logger.info(f"Deletando documentos {docs_ids} | tenant={tenant_id}")
    try:
        await service.deletar_documento_usuario(tenant_id, docs_ids)
        logger.info(f"Documentos | total={len(docs_ids)} deletados com sucesso")
        return {"mensagem": "Documentos deletados com sucesso"}
    except ValueError as e:
        logger.warning(str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Erro ao deletar documentos: {e}")
        raise HTTPException(status_code=500, detail=str(e))
