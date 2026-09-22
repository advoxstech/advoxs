import asyncio
import os
import uuid

import redis.asyncio as aioredis
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

redis_host = os.getenv("REDIS_HOST")
redis_port = os.getenv("REDIS_PORT")
redis_password = os.getenv("REDIS_PASSWORD")
DEFAULT_DEBOUNCE_SECONDS = 12


def configured_debounce_seconds() -> int:
    """Lê o intervalo de agrupamento sem tornar uma configuração inválida
    capaz de interromper o atendimento."""
    raw_value = os.getenv("MESSAGE_DEBOUNCE_SECONDS", str(DEFAULT_DEBOUNCE_SECONDS))
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("MESSAGE_DEBOUNCE_SECONDS inválido; usando padrão | value={}", raw_value)
        return DEFAULT_DEBOUNCE_SECONDS
    if value < 1:
        logger.warning(
            "MESSAGE_DEBOUNCE_SECONDS deve ser positivo; usando padrão | value={}", value
        )
        return DEFAULT_DEBOUNCE_SECONDS
    return value


async def debounce_messages(
    message: str,
    conversation_id: str,
    redis_host: str = redis_host,
    redis_port: int = redis_port,
    debounce_seconds: int | None = None,
) -> dict:
    if debounce_seconds is None:
        debounce_seconds = configured_debounce_seconds()
    logger.info(
        "Iniciando debounce | conversation_id={} | debounce_seconds={}",
        conversation_id,
        debounce_seconds,
    )

    r = aioredis.Redis(
        host=redis_host, port=redis_port, password=redis_password, decode_responses=True
    )

    exec_id = str(uuid.uuid4())
    buffer_key = f"whatsapp:buffer:{conversation_id}"
    timer_key = f"whatsapp:timer:{conversation_id}"

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
