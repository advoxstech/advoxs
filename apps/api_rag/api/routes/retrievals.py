from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

from api.security import verify_api_key
from constants import SYSTEM_TENANT_ID
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


@router_retrieval.post("/system")
async def retrieval_system(
    body: SystemRetrievalRequest,
    service: RetrievalService = Depends(get_retrieval),
    security: str = Depends(verify_api_key),
):
    """Busca na base de conhecimento da plataforma (compartilhada), por categoria."""
    try:
        logger.info(f"Busca sistema | base={body.base} | mensagem={body.message}")
        results = await service.search_hybrid(
            query=body.message,
            tenant_id=SYSTEM_TENANT_ID,
            extra_filters={"base": body.base},
        )
        return {"results": results}
    except ValueError as e:
        logger.warning(f"Erro de busca de informações: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Erro ao buscar informações: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router_retrieval.post("/users")
async def retrieval_users(
    body: UsersRetrievalRequest,
    service: RetrievalService = Depends(get_retrieval),
    security: str = Depends(verify_api_key),
):
    """Busca nos documentos enviados pelo contato, escopada por tenant + conversa."""
    try:
        logger.info(
            f"Busca usuário | tenant={body.tenant_id} | conversa={body.conversation_id}"
            f" | mensagem={body.message}"
        )
        results = await service.search_hybrid(
            query=body.message,
            tenant_id=body.tenant_id,
            extra_filters={"conversation_id": body.conversation_id},
        )
        return {"results": results}
    except ValueError as e:
        logger.warning(f"Erro de busca de informações: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Erro ao buscar informações: {e}")
        raise HTTPException(status_code=500, detail=str(e))
