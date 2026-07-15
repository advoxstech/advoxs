import os
import secrets
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse
from services.concat_messages import debounce_messages
from services.call_agent import run_agent, DB_URI
from services.summarize import summarize_conversation
from services.update_context import add_context_messages
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
    descriptografou as credenciais do WhatsApp antes de chamar aqui.

    `send_to_whatsapp=False` (usado pelo playground de admin) roda o grafo
    normalmente mas pula o envio pela Graph API — phone_number_id/access_token
    ficam vazios nesse caso.
    """

    tenant_id: str
    contact_phone_number: str
    message: str = ""
    attachments: list = Field(default_factory=list)
    phone_number_id: str = ""
    access_token: str = ""
    send_to_whatsapp: bool = True
    end_customer_billing: dict | None = None


class SummaryMessageIn(BaseModel):
    sender_type: str
    content: str


class SummaryRequest(BaseModel):
    messages: list[SummaryMessageIn]


class ContextMessageIn(BaseModel):
    role: Literal["contact", "attendant"]
    content: str


class ContextRequest(BaseModel):
    messages: list[ContextMessageIn] = Field(min_length=1)


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
        response, tokens_used, current_agent = await run_agent(
            message=messages["combined_message"],
            attachments=body.attachments,
            conversation_id=thread_id,
            number_whatsapp=body.contact_phone_number,
            end_customer_billing=body.end_customer_billing,
        )

        delivery_failures: list[int] = []
        if body.send_to_whatsapp:
            logger.info(
                "Enviando {} resposta(s) via WhatsApp | thread_id={}",
                len(response),
                thread_id,
            )
            async with WhatsAppClient(
                body.phone_number_id, body.access_token
            ) as client:
                for i, msg in enumerate(response):
                    result = await client.send_text_message(
                        body.contact_phone_number, msg
                    )
                    if not result.get("success"):
                        logger.warning(
                            "Falha ao entregar mensagem via WhatsApp | thread_id={} índice={} erro={}",
                            thread_id,
                            i,
                            result.get("error"),
                        )
                        delivery_failures.append(i)
        else:
            logger.info(
                "send_to_whatsapp=False — envio pulado | thread_id={}", thread_id
            )

        # Devolve as respostas, os tokens da execução e as falhas de entrega
        # para o chamador (`worker`) persistir em `messages` e debitar
        # créditos — a cobrança independe da entrega ter funcionado (o custo
        # do LLM já ocorreu).
        return {
            "responses": response,
            "tokens_used": tokens_used,
            "current_agent": current_agent,
            "delivery_failures": delivery_failures,
        }
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


@app.post("/conversations/{thread_id}/context", dependencies=[Depends(verify_api_key)])
async def add_context(thread_id: str, body: ContextRequest):
    """Anexa mensagens do takeover humano ao checkpoint — sem rodar o grafo.

    Chamado pelo api (resposta do atendente) e pelo worker (mensagem do
    contato em modo human/saldo esgotado). Sem LLM, sem débito de créditos.
    """
    try:
        added = await add_context_messages(
            thread_id,
            [{"role": m.role, "content": m.content} for m in body.messages],
        )
    except Exception:
        logger.exception("Erro ao anexar contexto | thread_id={}", thread_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao anexar contexto.",
        )
    return {"added": added}


@app.post("/summaries", dependencies=[Depends(verify_api_key)])
async def summarize(body: SummaryRequest):
    if not body.messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sem mensagens para resumir",
        )

    try:
        summary, tokens_used = await summarize_conversation(
            [
                {"sender_type": m.sender_type, "content": m.content}
                for m in body.messages
            ]
        )
    except Exception:
        logger.exception("Erro ao gerar resumo")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao gerar resumo.",
        )

    return {"summary": summary, "tokens_used": tokens_used}
