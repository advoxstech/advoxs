import logging

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import settings

_pool: ArqRedis | None = None

# Fila da base de conhecimento, consumida pelo container worker-ingestion um
# arquivo por vez. Espelhado em apps/worker/app/tasks/knowledge_base.py.
INGESTION_QUEUE = "arq:ingestion"


async def enqueue_file_task(arq: ArqRedis, *args, **kwargs):
    """O trigger já persistiu a tarefa; falha de Redis não desfaz a aceitação."""
    try:
        return await arq.enqueue_job(*args, **kwargs)
    except Exception:
        logging.getLogger(__name__).warning("Tarefa de arquivo aguardando recuperação")
        return None


async def get_arq_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def close_arq_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
