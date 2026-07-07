import redis.asyncio as aioredis
import asyncio
import uuid
from loguru import logger
from dotenv import load_dotenv
import os

load_dotenv()

redis_host = os.getenv("REDIS_HOST")
redis_port = os.getenv("REDIS_PORT")
redis_password = os.getenv("REDIS_PASSWORD")

async def debounce_messages(
    message: str,
    conversation_id: str,
    redis_host: str = redis_host,
    redis_port: int = redis_port,
    debounce_seconds: int = 5,
) -> dict:
    logger.info(
        "Iniciando debounce | conversation_id={} | debounce_seconds={}",
        conversation_id,
        debounce_seconds,
    )

    r = aioredis.Redis(host=redis_host, port=redis_port, password=redis_password, decode_responses=True)

    exec_id = str(uuid.uuid4())
    buffer_key = f"whatsapp:buffer:{conversation_id}"
    timer_key  = f"whatsapp:timer:{conversation_id}"

    await r.rpush(buffer_key, message)
    await r.expire(buffer_key, debounce_seconds + 10)
    await r.set(timer_key, exec_id, ex=debounce_seconds + 2)

    logger.info(
        "Mensagem adicionada ao buffer | exec_id={} | conversation_id={}",
        exec_id[:8],
        conversation_id,
    )
    await asyncio.sleep(debounce_seconds)

    current_exec = await r.get(timer_key)

    if current_exec != exec_id:
        logger.info(
            "Execução cancelada por outra execução | exec_id={} | conversation_id={}",
            exec_id[:8],
            conversation_id,
        )
        await r.aclose()
        return {"combined_message": None, "other_exec_is_running": True}

    pipe = r.pipeline()
    pipe.lrange(buffer_key, 0, -1)
    pipe.delete(buffer_key)
    pipe.delete(timer_key)
    results = await pipe.execute()

    await r.aclose()

    messages = results[0]

    if not messages:
        logger.info(
            "Buffer vazio após debounce | exec_id={} | conversation_id={}",
            exec_id[:8],
            conversation_id,
        )
        return {"combined_message": None, "other_exec_is_running": True}

    combined_message = "\n".join(messages)
    logger.info(
        "Mensagens consolidadas | exec_id={} | conversation_id={} | total={}",
        exec_id[:8],
        conversation_id,
        len(messages),
    )

    return {"combined_message": combined_message, "other_exec_is_running": False}
