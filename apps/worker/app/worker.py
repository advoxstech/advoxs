import httpx
from arq.connections import RedisSettings

from app.config import settings
from app.db import create_engine_and_factory
from app.tasks.knowledge_base import ingest_knowledge_base_file
from app.tasks.messages import process_inbound_message


async def startup(ctx: dict) -> None:
    engine, session_factory = create_engine_and_factory()
    ctx["engine"] = engine
    ctx["session_factory"] = session_factory
    # O agents pode demorar (debounce ~5s + LLM + envio WhatsApp) — timeout largo.
    ctx["http"] = httpx.AsyncClient(
        base_url=settings.agents_service_url, timeout=httpx.Timeout(300.0)
    )
    # A ingestão do api_rag é síncrona (parsing + embeddings + Qdrant) — timeout largo.
    ctx["rag_http"] = httpx.AsyncClient(
        base_url=settings.rag_api_url, timeout=httpx.Timeout(300.0)
    )


async def shutdown(ctx: dict) -> None:
    await ctx["http"].aclose()
    await ctx["rag_http"].aclose()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [ingest_knowledge_base_file, process_inbound_message]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = startup
    on_shutdown = shutdown
    job_timeout = 600
