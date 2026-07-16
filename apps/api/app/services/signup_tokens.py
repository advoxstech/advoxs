"""Token one-time de auto-login pós-cadastro.

Duas chaves no Redis, ambas de uso único (GETDEL) e TTL curto:
- signup:handoff:{session_id} → token em claro. Entregue UMA vez pelo
  GET /signup/status — o navegador legítimo está pollando desde antes da
  conta existir, então sempre chega primeiro; a URL com session_id vazada
  depois não destrava mais nada.
- signup:token:{sha256(token)} → user_id. Trocado UMA vez por par JWT no
  POST /auth/signup-login — em repouso só o hash.
"""

import hashlib
import secrets

from redis.asyncio import Redis

TOKEN_TTL_SECONDS = 900
_HANDOFF_PREFIX = "signup:handoff:"
_TOKEN_PREFIX = "signup:token:"


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def store_login_token(redis: Redis, session_id: str, user_id) -> None:
    token = secrets.token_urlsafe(32)
    await redis.set(f"{_HANDOFF_PREFIX}{session_id}", token, ex=TOKEN_TTL_SECONDS)
    await redis.set(f"{_TOKEN_PREFIX}{_sha256(token)}", str(user_id), ex=TOKEN_TTL_SECONDS)


async def claim_handoff_token(redis: Redis, session_id: str) -> str | None:
    return await redis.getdel(f"{_HANDOFF_PREFIX}{session_id}")


async def consume_login_token(redis: Redis, token: str) -> str | None:
    return await redis.getdel(f"{_TOKEN_PREFIX}{_sha256(token)}")
