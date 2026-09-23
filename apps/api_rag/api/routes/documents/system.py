from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api.security import verify_api_key
from clients.qdrant import QdrantClient
from database.repositories.documento import DocumentoRepository
from database.session import get_session
from safe_logging import safe_error
from services.documents.main import DocumentoService

router_doc_system = APIRouter(prefix="/documents/system")


def get_qdrant():
    return QdrantClient()


async def get_repo(session: AsyncSession = Depends(get_session)):
    return DocumentoRepository(session=session)


async def get_service(
    repo: DocumentoRepository = Depends(get_repo),
    qdrant: QdrantClient = Depends(get_qdrant),
) -> DocumentoService:
    return DocumentoService(repo=repo, qdrant=qdrant)


# ── Documentos do Sistema (base da plataforma, compartilhada) ────────────


@router_doc_system.post("/insert")
async def inserir_documento(
    base: str = Form(...),
    id_drive: str = Form(...),
    file: UploadFile = File(...),
    service: DocumentoService = Depends(get_service),
    security: str = Depends(verify_api_key),
):
    try:
        files = [file]
        logger.info(f"Recebendo {len(files)} arquivos | base={base}")
        await service.inserir_documento_sistema(files, base, id_drive)
        return {"mensagem": "Documentos inseridos com sucesso"}
    except ValueError as exc:
        logger.warning("Erro de validação ao inserir documento | error_type={}", safe_error(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Erro ao inserir documento | error_type={}", safe_error(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router_doc_system.delete("/delete")
async def deletar_documentos(
    docs_ids: list[str] = Query(...),
    service: DocumentoService = Depends(get_service),
    security: str = Depends(verify_api_key),
):
    logger.info("Deletando documentos | total={}", len(docs_ids))
    try:
        await service.deletar_documento_sistema(docs_ids)
        logger.info(f"Documentos | total={len(docs_ids)} deletados com sucesso")
        return {"mensagem": "Documentos deletados com sucesso"}
    except ValueError as exc:
        logger.warning("Erro de validação ao deletar documentos | error_type={}", safe_error(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Erro ao deletar documentos | error_type={}", safe_error(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
