from unittest.mock import MagicMock

import pytest

import clients.retrieval as retrieval_module
from clients.retrieval import retrieval_usuario


class FakeAsyncClient:
    """Substitui httpx.AsyncClient capturando as chamadas post."""

    calls: list

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        FakeAsyncClient.calls.append((url, kwargs))
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"results": [{"text": "chunk"}]}
        return response


@pytest.fixture(autouse=True)
def fake_httpx(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr(retrieval_module.httpx, "AsyncClient", FakeAsyncClient)


async def test_retrieval_usuario_divide_thread_id_composto():
    results = await retrieval_usuario("tenant-1:5511999998888", "meu contrato")

    assert results == [{"text": "chunk"}]
    (_, kwargs) = FakeAsyncClient.calls[0]
    assert kwargs["json"] == {
        "tenant_id": "tenant-1",
        "conversation_id": "5511999998888",
        "message": "meu contrato",
    }


async def test_retrieval_usuario_sem_separador_usa_id_inteiro():
    await retrieval_usuario("id-legado", "pergunta")

    (_, kwargs) = FakeAsyncClient.calls[0]
    assert kwargs["json"]["tenant_id"] == "id-legado"
    assert kwargs["json"]["conversation_id"] == "id-legado"
