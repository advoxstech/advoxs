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
        conversation_id: thread_id composto "{tenant_id}:{contact_phone_number}" —
            dividido aqui para enviar tenant_id e conversation_id separados ao
            api_rag (que exige tenant_id em todo retrieval).
        message: Pergunta do usuário.
    """
    tenant_id, sep, contact_id = str(conversation_id).partition(":")
    if not sep:
        logger.warning(
            "conversation_id sem tenant_id (esperado 'tenant:contato'): {}", conversation_id
        )
        contact_id = tenant_id

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{RAG_API_URL}/retrieval/users",
                json={
                    "tenant_id": tenant_id,
                    "conversation_id": contact_id,
                    "message": message,
                },
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


# conversation_id reservado da base de conhecimento do escritório —
# documentos ingeridos pelo worker do monorepo com esse marcador.
KB_CONVERSATION_ID = "kb"


async def retrieval_escritorio(conversation_id: str, message: str) -> list[dict]:
    """Busca na base de conhecimento própria do escritório (tenant).

    Args:
        conversation_id: thread_id composto "{tenant_id}:{contact_phone_number}" —
            só o tenant_id é usado; a busca é sempre em conversation_id="kb".
        message: Pergunta do usuário.
    """
    tenant_id, _, _ = str(conversation_id).partition(":")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{RAG_API_URL}/retrieval/users",
                json={
                    "tenant_id": tenant_id,
                    "conversation_id": KB_CONVERSATION_ID,
                    "message": message,
                },
                headers=HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            logger.debug("Retrieval escritório retornou {} chunks | tenant={}", len(results), tenant_id)
            return results

    except httpx.HTTPStatusError as e:
        logger.error("Erro HTTP no retrieval escritório | status={} | response={}", e.response.status_code, e.response.text)
        return []
    except Exception as e:
        logger.error("Erro ao consultar retrieval escritório | error={}", str(e))
        return []
