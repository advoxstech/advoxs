import hashlib
from unittest.mock import AsyncMock

from app.services.signup_tokens import (
    claim_handoff_token,
    consume_login_token,
    store_login_token,
)


async def test_store_grava_as_duas_chaves_com_ttl() -> None:
    redis = AsyncMock()

    await store_login_token(redis, "cs_test_123", "user-uuid")

    assert redis.set.await_count == 2
    calls = {call.args[0]: call for call in redis.set.await_args_list}
    handoff_call = calls["signup:handoff:cs_test_123"]
    token = handoff_call.args[1]
    assert handoff_call.kwargs["ex"] == 900

    sha = hashlib.sha256(token.encode()).hexdigest()
    token_call = calls[f"signup:token:{sha}"]
    assert token_call.args[1] == "user-uuid"
    assert token_call.kwargs["ex"] == 900


async def test_claim_faz_getdel_do_handoff() -> None:
    redis = AsyncMock()
    redis.getdel.return_value = "token-em-claro"

    result = await claim_handoff_token(redis, "cs_test_123")

    assert result == "token-em-claro"
    redis.getdel.assert_awaited_once_with("signup:handoff:cs_test_123")


async def test_consume_faz_getdel_pelo_hash() -> None:
    redis = AsyncMock()
    redis.getdel.return_value = "user-uuid"

    result = await consume_login_token(redis, "meu-token")

    sha = hashlib.sha256(b"meu-token").hexdigest()
    redis.getdel.assert_awaited_once_with(f"signup:token:{sha}")
    assert result == "user-uuid"
