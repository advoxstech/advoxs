from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

from api.security import verify_api_key
from constants import SYSTEM_TENANT_ID
from safe_logging import safe_error, safe_identifier
from services.retrieval.main import RetrievalService

router_retrieval = APIRouter(prefix="/retrieval")


def get_retrieval():
    return RetrievalService()


class SystemRetrievalRequest(BaseModel):
    base: str
    message: str


class UsersRetrievalRequest(BaseModel):
    tenant_id: str
    conversation_id: str
    message: str
    doc_ids: list[str] | None = None


@router_retrieval.post("/system")
async def retrieval_system(
    body: SystemRetrievalRequest,
    service: RetrievalService = Depends(get_retrieval),
    security: str = Depends(verify_api_key),
):
    """Busca na base de conhecimento da plataforma (compartilhada), por categoria."""
    try:
        logger.info("Busca sistema | base={} | caracteres={}", body.base, len(body.message))
        results = await service.search_hybrid(
            query=body.message,
            tenant_id=SYSTEM_TENANT_ID,
            extra_filters={"base": body.base},
        )
        return {"results": results}
    except ValueError as exc:
        logger.warning("Parâmetros inválidos na busca | error_type={}", safe_error(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Erro ao buscar informações | error_type={}", safe_error(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router_retrieval.post("/users")
async def retrieval_users(
    body: UsersRetrievalRequest,
    service: RetrievalService = Depends(get_retrieval),
    security: str = Depends(verify_api_key),
):
    """Busca nos documentos enviados pelo contato, escopada por tenant + conversa.

    `doc_ids`, quando informado, restringe a busca a esse subconjunto de
    documentos (ex: os arquivos anexados a um agente específico do escritório)
    — omitido, busca em todo o pool de documentos daquela conversation_id.
    """
    try:
        logger.info(
            "Busca usuário | tenant_ref={} | conversation_ref={} | caracteres={}",
            safe_identifier(body.tenant_id),
            safe_identifier(body.conversation_id),
            len(body.message),
        )
        extra_filters = {"conversation_id": body.conversation_id}
        if body.doc_ids:
            extra_filters["doc_id"] = body.doc_ids
        results = await service.search_hybrid(
            query=body.message,
            tenant_id=body.tenant_id,
            extra_filters=extra_filters,
        )
        return {"results": results}
    except ValueError as exc:
        logger.warning("Parâmetros inválidos na busca | error_type={}", safe_error(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Erro ao buscar informações | error_type={}", safe_error(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
