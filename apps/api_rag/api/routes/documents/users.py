from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api.security import verify_api_key
from clients.qdrant import QdrantClient
from database.repositories.documento import DocumentoRepository
from database.session import get_session
from safe_logging import safe_error, safe_identifier
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
    doc_id: str | None = Form(default=None),
    file: UploadFile = File(...),
    service: DocumentoService = Depends(get_service),
    security: str = Depends(verify_api_key),
):
    try:
        files = [file]
        logger.info(
            "Recebendo arquivos | total={} | tenant_ref={}",
            len(files),
            safe_identifier(tenant_id),
        )
        await service.inserir_documento_usuario(files, tenant_id, conversation_id, doc_id=doc_id)
        return {"mensagem": "Documentos inseridos com sucesso"}
    except ValueError as exc:
        logger.warning("Erro de validação ao inserir documento | error_type={}", safe_error(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Erro ao inserir documento | error_type={}", safe_error(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router_doc_users.delete("/delete")
async def deletar_documentos(
    tenant_id: str = Query(...),
    docs_ids: list[str] = Query(...),
    service: DocumentoService = Depends(get_service),
    security: str = Depends(verify_api_key),
):
    logger.info(
        "Deletando documentos | total={} | tenant_ref={}",
        len(docs_ids),
        safe_identifier(tenant_id),
    )
    try:
        await service.deletar_documento_usuario(tenant_id, docs_ids)
        logger.info(f"Documentos | total={len(docs_ids)} deletados com sucesso")
        return {"mensagem": "Documentos deletados com sucesso"}
    except ValueError as exc:
        logger.warning("Erro de validação ao deletar documentos | error_type={}", safe_error(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Erro ao deletar documentos | error_type={}", safe_error(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
