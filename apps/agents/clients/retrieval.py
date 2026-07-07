# agents/clients/rag.py

import httpx
import os
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

RAG_API_URL = os.getenv("RAG_API_URL")
RAG_API_KEY = os.getenv("RAG_API_KEY")

HEADERS = {"Authorization": RAG_API_KEY}


async def retrieval_sistema(base: str, message: str) -> list[dict]:
    """Busca documentos gerais do sistema.

    Args:
        base: Base de documentos a consultar (ex: "juridico").
        message: Pergunta do usuário.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{RAG_API_URL}/retrieval/system",
                json={"base": base, "message": message},
                headers=HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            logger.debug("Retrieval sistema retornou {} chunks | base={}", len(results), base)
            return results

    except httpx.HTTPStatusError as e:
        logger.error("Erro HTTP no retrieval sistema | status={} | response={}", e.response.status_code, e.response.text)
        return []
    except Exception as e:
        logger.error("Erro ao consultar retrieval sistema | error={}", str(e))
        return []


async def retrieval_usuario(conversation_id: str, message: str) -> list[dict]:
    """Busca documentos privados do usuário.

    Args:
        conversation_id: ID da conversa/usuário.
        message: Pergunta do usuário.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{RAG_API_URL}/retrieval/users",
                json={"conversation_id": str(conversation_id), "message": message},
                headers=HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            logger.debug("Retrieval usuário retornou {} chunks | conversation_id={}", len(results), conversation_id)
            return results

    except httpx.HTTPStatusError as e:
        logger.error("Erro HTTP no retrieval usuário | status={} | response={}", e.response.status_code, e.response.text)
        return []
    except Exception as e:
        logger.error("Erro ao consultar retrieval usuário | error={}", str(e))
        return []
