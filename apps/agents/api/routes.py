import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse
from services.concat_messages import debounce_messages
from services.call_agent import run_agent, DB_URI
from clients.whatsapp import WhatsAppClient
from agents.registry import AGENTS_REGISTRY
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from loguru import logger
from pydantic import BaseModel, Field

AGENTS_API_KEY = os.getenv("AGENTS_API_KEY")


async def verify_api_key(authorization: str | None = Header(default=None)):
    """Auth de serviço interno: o `api` chama este serviço com o header
    `Authorization: <AGENTS_API_KEY>`. Se a env não estiver setada (dev local),
    a verificação é ignorada."""
    if not AGENTS_API_KEY:
        return
    if not authorization or not secrets.compare_digest(authorization, AGENTS_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Key inválida ou ausente",
        )


class IncomingMessage(BaseModel):
    """Contrato interno: o `api` já resolveu o tenant (via phone_number_id do
    webhook da Meta), validou o estado da conversa (agent|human) e
    descriptografou as credenciais do WhatsApp antes de chamar aqui."""

    tenant_id: str
    contact_phone_number: str
    message: str = ""
    attachments: list = Field(default_factory=list)
    phone_number_id: str
    access_token: str


app = FastAPI()


@app.get("/agents")
async def list_agents():
    return AGENTS_REGISTRY


@app.post("/messages", dependencies=[Depends(verify_api_key)])
async def receive(body: IncomingMessage):
    # thread_id escopado por tenant: isola checkpoint (LangGraph), debounce
    # (Redis) e docs de usuário (RAG) entre escritórios.
    thread_id = f"{body.tenant_id}:{body.contact_phone_number}"

    if body.attachments:
        logger.debug("Anexos recebidos | attachments={}", body.attachments)

    if not body.message and not body.attachments:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mensagem inválida",
        )

    logger.info(
        "Nova mensagem recebida | tenant_id={} | thread_id={} | message={}",
        body.tenant_id,
        thread_id,
        body.message,
    )

    try:
        messages = await debounce_messages(
            message=body.message or str(body.attachments),
            conversation_id=thread_id,
        )
    except Exception:
        logger.exception("Erro no debounce | thread_id={}", thread_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Erro ao processar buffer de mensagens (Redis indisponível?)",
        )

    if messages["other_exec_is_running"]:
        logger.info("Execução em andamento, ignorando | thread_id={}", thread_id)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"message": "Execução em andamento"},
        )

    try:
        logger.info("Encaminhando mensagem ao agente | thread_id={}", thread_id)
        response, tokens_used = await run_agent(
            message=messages["combined_message"],
            attachments=body.attachments,
            conversation_id=thread_id,
            number_whatsapp=body.contact_phone_number,
        )

        logger.info(
            "Enviando {} resposta(s) via WhatsApp | thread_id={}",
            len(response),
            thread_id,
        )

        async with WhatsAppClient(body.phone_number_id, body.access_token) as client:
            for msg in response:
                await client.send_text_message(body.contact_phone_number, msg)

        # Devolve as respostas e os tokens da execução para o chamador
        # (`worker`) persistir em `messages` e debitar os créditos.
        return {"responses": response, "tokens_used": tokens_used}
    except Exception:
        logger.exception("Erro ao chamar o agente | thread_id={}", thread_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro interno ao processar resposta do agente.",
        )


@app.delete("/conversations/{thread_id}", dependencies=[Depends(verify_api_key)])
async def delete_conversation(thread_id: str):
    logger.info("Deletando conversa | thread_id={}", thread_id)
    try:
        async with AsyncPostgresSaver.from_conn_string(DB_URI) as checkpointer:
            await checkpointer.adelete_thread(thread_id)
    except Exception:
        logger.exception("Erro ao deletar conversa | thread_id={}", thread_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao deletar conversa.",
        )
    logger.info("Conversa deletada | thread_id={}", thread_id)
    return {"deleted": thread_id}
