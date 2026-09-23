import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from loguru import logger
from pydantic import BaseModel, Field

from agents.registry import AGENTS_REGISTRY
from clients.whatsapp import WhatsAppClient
from clients.zapi import ZApiClient
from config_validation import validate_environment
from core.safe_logging import safe_error, safe_identifier
from services.call_agent import DB_URI, run_agent
from services.concat_messages import debounce_messages
from services.document_storage import resolve_authorized_path, start_cleanup_loop
from services.summarize import summarize_conversation
from services.update_context import add_context_messages, replace_context_messages

validate_environment("agents")
AGENTS_API_KEY = os.getenv("AGENTS_API_KEY")


async def verify_api_key(authorization: str | None = Header(default=None)):
    """Auth de serviço interno: o `api` chama este serviço com o header
    `Authorization: <AGENTS_API_KEY>`. Se a env não estiver setada (dev local),
    a verificação é ignorada."""
    if not AGENTS_API_KEY:
        if os.getenv("APP_ENV") == "production":
            raise HTTPException(status_code=503, detail="Autenticação interna indisponível")
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

    `send_to_whatsapp=False` (usado pelo worker e pelo playground de admin)
    roda o grafo normalmente mas pula o envio pela Graph API — as credenciais
    do provedor ficam vazias nesse caso.

    `agents`: a lista completa de agentes do tenant, resolvida pelo chamador
    (worker/api) — nunca lida pelo agents service do Postgres principal.

    `whatsapp_provider`: qual cliente de canal usar no envio ("meta" default
    | "zapi") — decide entre WhatsAppClient (Graph API) e ZApiClient. Os
    campos `zapi_*` só são preenchidos pelo chamador quando o provedor do
    tenant é "zapi"; `phone_number_id`/`access_token` seguem exclusivos do
    provedor "meta".
    """

    tenant_id: str
    contact_phone_number: str
    message: str = ""
    attachments: list = Field(default_factory=list)
    whatsapp_provider: str = "meta"
    phone_number_id: str = ""
    access_token: str = ""
    zapi_instance_id: str = ""
    zapi_token: str = ""
    zapi_client_token: str = ""
    send_to_whatsapp: bool = True
    agents: list[dict] = Field(default_factory=list)


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


class ReplaceContextRequest(BaseModel):
    messages: list[ContextMessageIn] = Field(default_factory=list)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    asyncio.create_task(start_cleanup_loop())
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/agents")
async def list_agents():
    return AGENTS_REGISTRY


@app.get("/generated-documents/{doc_id}")
async def get_generated_document(doc_id: str, token: str | None = None):
    """Entrega o PDF somente enquanto seu token temporário estiver válido."""
    path = resolve_authorized_path(doc_id, token)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Documento não encontrado."
        )
    return FileResponse(path, media_type="application/pdf")


@app.post("/messages", dependencies=[Depends(verify_api_key)])
async def receive(body: IncomingMessage):
    # thread_id escopado por tenant: isola checkpoint (LangGraph), debounce
    # (Redis) e docs de usuário (RAG) entre escritórios.
    thread_id = f"{body.tenant_id}:{body.contact_phone_number}"

    if body.attachments:
        logger.debug("Anexos recebidos | total={}", len(body.attachments))

    if not body.message and not body.attachments:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mensagem inválida",
        )

    logger.info(
        "Nova mensagem recebida | tenant_id={} | conversation_ref={} | caracteres={}",
        body.tenant_id,
        safe_identifier(thread_id),
        len(body.message or ""),
    )

    try:
        messages = await debounce_messages(
            message=body.message or str(body.attachments),
            conversation_id=thread_id,
        )
    except Exception as exc:
        logger.error(
            "Erro no debounce | conversation_ref={} error_type={}",
            safe_identifier(thread_id),
            safe_error(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Erro ao processar buffer de mensagens (Redis indisponível?)",
        )

    if messages["other_exec_is_running"]:
        logger.info(
            "Execução em andamento, ignorando | conversation_ref={}",
            safe_identifier(thread_id),
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"message": "Execução em andamento"},
        )

    try:
        logger.info(
            "Encaminhando mensagem ao agente | conversation_ref={}",
            safe_identifier(thread_id),
        )
        response, usage, current_agent, current_agent_id, generated_documents = await run_agent(
            message=messages["combined_message"],
            attachments=body.attachments,
            conversation_id=thread_id,
            number_whatsapp=body.contact_phone_number,
            agents=body.agents,
        )

        delivery_failures: list[int] = []
        # `documents` carrega o custo fixo em crédito de cada documento
        # gerado (ver agents/tools.py) — devolvido ao chamador mesmo quando
        # send_to_whatsapp=False (playground/conversa de teste), pra que o
        # débito de créditos aconteça igual independente do canal de entrega.
        documents: list[dict] = [{**doc, "delivered": False} for doc in generated_documents]

        if body.send_to_whatsapp:
            logger.info(
                "Enviando {} resposta(s) via WhatsApp | conversation_ref={} provider={}",
                len(response),
                safe_identifier(thread_id),
                body.whatsapp_provider,
            )
            if body.whatsapp_provider == "zapi":
                whatsapp_client_cm = ZApiClient(
                    body.zapi_instance_id, body.zapi_token, body.zapi_client_token or None
                )
            else:
                whatsapp_client_cm = WhatsAppClient(body.phone_number_id, body.access_token)

            async with whatsapp_client_cm as client:
                for i, msg in enumerate(response):
                    result = await client.send_text_message(body.contact_phone_number, msg)
                    if not result.get("success"):
                        logger.warning(
                            "Falha ao entregar mensagem via WhatsApp | "
                            "conversation_ref={} índice={} erro={}",
                            safe_identifier(thread_id),
                            i,
                            result.get("error"),
                        )
                        delivery_failures.append(i)

                for doc in documents:
                    result = await client.send_document_message(
                        body.contact_phone_number, doc["link"], filename=doc["filename"]
                    )
                    doc["delivered"] = bool(result.get("success"))
                    if not doc["delivered"]:
                        logger.warning(
                            "Falha ao entregar documento via WhatsApp | "
                            "conversation_ref={} documento_ref={} erro={}",
                            safe_identifier(thread_id),
                            safe_identifier(doc.get("id") or doc.get("filename", "documento")),
                            result.get("error"),
                        )
        else:
            logger.info(
                "send_to_whatsapp=False — envio pulado | conversation_ref={}",
                safe_identifier(thread_id),
            )

        # Devolve as respostas, os tokens da execução, os documentos gerados
        # e as falhas de entrega para o chamador (`worker`/`api`) persistir
        # em `messages` e debitar créditos — a cobrança independe da entrega
        # ter funcionado (o custo do LLM/da geração já ocorreu).
        return {
            "responses": response,
            "tokens_used": usage["total_tokens"],
            "tokens_input": usage["input_tokens"],
            "tokens_output": usage["output_tokens"],
            "current_agent": current_agent,
            "current_agent_id": current_agent_id,
            "delivery_failures": delivery_failures,
            "documents": documents,
        }
    except Exception as exc:
        logger.error(
            "Erro ao chamar o agente | conversation_ref={} error_type={}",
            safe_identifier(thread_id),
            safe_error(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro interno ao processar resposta do agente.",
        )


@app.delete("/conversations/{thread_id}", dependencies=[Depends(verify_api_key)])
async def delete_conversation(thread_id: str):
    logger.info("Deletando conversa | conversation_ref={}", safe_identifier(thread_id))
    try:
        async with AsyncPostgresSaver.from_conn_string(DB_URI) as checkpointer:
            await checkpointer.adelete_thread(thread_id)
    except Exception as exc:
        logger.error(
            "Erro ao deletar conversa | conversation_ref={} error_type={}",
            safe_identifier(thread_id),
            safe_error(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao deletar conversa.",
        )
    logger.info("Conversa deletada | conversation_ref={}", safe_identifier(thread_id))
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
    except Exception as exc:
        logger.error(
            "Erro ao anexar contexto | conversation_ref={} error_type={}",
            safe_identifier(thread_id),
            safe_error(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao anexar contexto.",
        )
    return {"added": added}


@app.put("/conversations/{thread_id}/context", dependencies=[Depends(verify_api_key)])
async def replace_context(thread_id: str, body: ReplaceContextRequest):
    """Recria o checkpoint sem uma resposta que foi descartada pelo worker."""
    try:
        replaced = await replace_context_messages(
            thread_id,
            [{"role": m.role, "content": m.content} for m in body.messages],
        )
    except Exception as exc:
        logger.error(
            "Erro ao substituir contexto | conversation_ref={} error_type={}",
            safe_identifier(thread_id),
            safe_error(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao substituir contexto.",
        )
    return {"replaced": replaced}


@app.post("/summaries", dependencies=[Depends(verify_api_key)])
async def summarize(body: SummaryRequest):
    if not body.messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sem mensagens para resumir",
        )

    try:
        summary, usage = await summarize_conversation(
            [{"sender_type": m.sender_type, "content": m.content} for m in body.messages]
        )
    except Exception as exc:
        logger.error("Erro ao gerar resumo | error_type={}", safe_error(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao gerar resumo.",
        )

    return {
        "summary": summary,
        "tokens_used": usage["total_tokens"],
        "tokens_input": usage["input_tokens"],
        "tokens_output": usage["output_tokens"],
    }
