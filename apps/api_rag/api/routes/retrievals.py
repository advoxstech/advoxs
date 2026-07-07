from fastapi import APIRouter, HTTPException, Depends, Body
from loguru import logger
from services.retrieval.main import RetrievalService
from api.security import verify_api_key
from dotenv import load_dotenv
import os

load_dotenv()


COLLECTION_SISTEMA = os.getenv("COLLECTION_SISTEMA")
COLLECTION_USERS = os.getenv("COLLECTION_USERS")


router_retrieval = APIRouter(prefix="/retrieval")

def get_retrieval():
    return RetrievalService()


@router_retrieval.post("/system")
async def retrieval_system(
    base: str = Body(...),
    message: str = Body(...),
    service: RetrievalService = Depends(get_retrieval),
    security: str = Depends(verify_api_key),
):
    try:
        logger.info(f"Solicitação de busca para base: {base} | messagem: {message}")
        results = await service.search_hybrid(collection_name=COLLECTION_SISTEMA, query=message, payload_filter={"base": base})
        return {"results": results}
    except ValueError as e:
        logger.warning(f"Erro de busca de informações: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Erro ao buscar informações: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router_retrieval.post("/users")
async def retrieval_users(
    conversation_id: str = Body(...),
    message: str = Body(...),
    service: RetrievalService = Depends(get_retrieval),
    security: str = Depends(verify_api_key),
):
    try:
        logger.info(f"Solicitação de busca para base: {conversation_id} | messagem: {message}")
        results = await service.search_hybrid(collection_name=COLLECTION_USERS, query=message, payload_filter={"conversation_id": conversation_id})
        return {"results": results}
    except ValueError as e:
        logger.warning(f"Erro de busca de informações: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Erro ao buscar informações: {e}")
        raise HTTPException(status_code=500, detail=str(e))

