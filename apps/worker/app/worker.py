import httpx
from arq import cron
from arq.connections import RedisSettings
from arq.worker import func

from app.config import settings
from app.db import create_engine_and_factory, create_system_engine_and_factory
from app.tasks.conversation_cleanup import (
    process_conversation_cleanup,
    recover_conversation_cleanup_jobs,
)
from app.tasks.follow_ups import deliver_follow_up_batch, recover_follow_up_batches
from app.tasks.inbound_outbox import recover_inbound_message_jobs
from app.tasks.knowledge_base import (
    INGESTION_QUEUE,
    ingest_knowledge_base_file,
    recover_drive_imports,
)
from app.tasks.messages import process_inbound_message
from app.tasks.outbound_outbox import deliver_outbound_message, recover_outbound_message_jobs
from app.tasks.team_emails import deliver_team_emails

# Um livro grande leva minutos no api_rag (lido e gravado em lotes) — a fila de
# ingestão espera bem mais que o atendimento, mas nunca para sempre.
INGESTION_RAG_TIMEOUT_SECONDS = 1500
INGESTION_JOB_TIMEOUT_SECONDS = 1800
# Turno de mensagem com anexo: leitura (ATTACHMENT_RAG_TIMEOUT_SECONDS) + agente.
# Menor que PROCESSING_LEASE (app/tasks/messages.py), que protege a ordem dos turnos.
TURN_JOB_TIMEOUT_SECONDS = 1800


async def startup(ctx: dict) -> None:
    engine, session_factory = create_engine_and_factory()
    ctx["engine"] = engine
    ctx["session_factory"] = session_factory
    system_engine, system_session_factory = create_system_engine_and_factory()
    ctx["system_engine"] = system_engine
    ctx["system_session_factory"] = system_session_factory
    # O agents pode demorar (debounce ~5s + LLM + envio WhatsApp) — timeout largo.
    ctx["http"] = httpx.AsyncClient(
        base_url=settings.agents_service_url, timeout=httpx.Timeout(300.0)
    )
    # Anexos de conversa vão síncronos ao api_rag (parsing + embeddings + Qdrant).
    ctx["rag_http"] = httpx.AsyncClient(base_url=settings.rag_api_url, timeout=httpx.Timeout(300.0))


async def shutdown(ctx: dict) -> None:
    await ctx["http"].aclose()
    await ctx["rag_http"].aclose()
    await ctx["engine"].dispose()
    await ctx["system_engine"].dispose()


async def ingestion_startup(ctx: dict) -> None:
    engine, session_factory = create_engine_and_factory()
    ctx["engine"] = engine
    ctx["session_factory"] = session_factory
    ctx["rag_http"] = httpx.AsyncClient(
        base_url=settings.rag_api_url, timeout=httpx.Timeout(INGESTION_RAG_TIMEOUT_SECONDS)
    )


async def ingestion_shutdown(ctx: dict) -> None:
    await ctx["rag_http"].aclose()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [
        deliver_team_emails,
        func(deliver_follow_up_batch, keep_result=0),
        recover_follow_up_batches,
        process_conversation_cleanup,
        # O turno pode esperar a leitura de um anexo grande (até 25 min no api_rag).
        func(process_inbound_message, timeout=TURN_JOB_TIMEOUT_SECONDS),
        recover_inbound_message_jobs,
        deliver_outbound_message,
        recover_outbound_message_jobs,
        recover_conversation_cleanup_jobs,
    ]
    cron_jobs = [
        cron(deliver_team_emails, second={10, 40}),
        cron(recover_drive_imports, second=30),
        cron(recover_inbound_message_jobs, second=0),
        cron(recover_outbound_message_jobs, second=0),
        cron(recover_conversation_cleanup_jobs, second=30),
        cron(recover_follow_up_batches, second=20),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = startup
    on_shutdown = shutdown
    job_timeout = 600


class IngestionWorkerSettings:
    """Base de conhecimento: um arquivo por vez, na fila própria (container worker-ingestion)."""

    functions = [func(ingest_knowledge_base_file, keep_result=0)]
    queue_name = INGESTION_QUEUE
    max_jobs = 1
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = ingestion_startup
    on_shutdown = ingestion_shutdown
    job_timeout = INGESTION_JOB_TIMEOUT_SECONDS
