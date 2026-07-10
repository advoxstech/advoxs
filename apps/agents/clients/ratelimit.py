"""Rate limiter simples (contador por segundo) para envio via Graph API.

Protege contra rajadas que ultrapassem o limite de mensagens/segundo por
número — mesma infraestrutura Redis já usada no debounce
(services/concat_messages.py), sem depender de biblioteca externa.
"""

import asyncio
import os

import redis.asyncio as aioredis
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = os.getenv("REDIS_PORT")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
WHATSAPP_RATE_LIMIT_PER_SECOND = int(os.getenv("WHATSAPP_RATE_LIMIT_PER_SECOND", "10"))

_MAX_WAIT_SECONDS = 5
_POLL_INTERVAL_SECONDS = 0.1


async def acquire_rate_limit_slot(phone_number_id: str) -> bool:
    """Consome 1 unidade do bucket do segundo atual para este número.

    Espera até _MAX_WAIT_SECONDS por uma vaga; devolve True se conseguiu,
    False se o teto de espera foi atingido (o chamador trata como falha
    transitória e decide se tenta de novo).
    """
    r = aioredis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, decode_responses=True
    )
    key = f"whatsapp:ratelimit:{phone_number_id}"
    waited = 0.0
    try:
        while True:
            current = await r.get(key)
            current_count = int(current) if current else 0
            if current_count < WHATSAPP_RATE_LIMIT_PER_SECOND:
                count = await r.incr(key)
                if count == 1:
                    await r.expire(key, 1)
                return True
            if waited >= _MAX_WAIT_SECONDS:
                logger.warning(
                    "Rate limit não liberado a tempo | phone_number_id={} limite={}",
                    phone_number_id, WHATSAPP_RATE_LIMIT_PER_SECOND,
                )
                return False
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            waited += _POLL_INTERVAL_SECONDS
    finally:
        await r.aclose()
