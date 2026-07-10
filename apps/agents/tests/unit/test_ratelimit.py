import asyncio
from unittest.mock import AsyncMock

import pytest

import clients.ratelimit as ratelimit_module
from clients.ratelimit import acquire_rate_limit_slot


class FakeRedis:
    def __init__(self):
        self.store: dict[str, int] = {}

    async def get(self, key):
        return self.store.get(key)

    async def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key, seconds):
        pass

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(ratelimit_module.aioredis, "Redis", lambda **kwargs: fake)
    return fake


class TestAcquireRateLimitSlot:
    async def test_libera_imediatamente_abaixo_do_limite(self, fake_redis, monkeypatch) -> None:
        monkeypatch.setattr(ratelimit_module, "WHATSAPP_RATE_LIMIT_PER_SECOND", 5)

        acquired = await acquire_rate_limit_slot("111222333")

        assert acquired is True
        assert fake_redis.store["whatsapp:ratelimit:111222333"] == 1

    async def test_nega_apos_esgotar_o_limite_do_segundo(self, fake_redis, monkeypatch) -> None:
        monkeypatch.setattr(ratelimit_module, "WHATSAPP_RATE_LIMIT_PER_SECOND", 1)
        fake_redis.store["whatsapp:ratelimit:111222333"] = 1

        acquired = await acquire_rate_limit_slot("111222333")

        assert acquired is False

    async def test_numeros_diferentes_tem_buckets_independentes(
        self, fake_redis, monkeypatch
    ) -> None:
        monkeypatch.setattr(ratelimit_module, "WHATSAPP_RATE_LIMIT_PER_SECOND", 1)
        fake_redis.store["whatsapp:ratelimit:AAA"] = 1

        acquired = await acquire_rate_limit_slot("BBB")

        assert acquired is True
