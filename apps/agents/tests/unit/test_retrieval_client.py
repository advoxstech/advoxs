from unittest.mock import AsyncMock, MagicMock

import pytest

import clients.retrieval as retrieval_module
from clients.retrieval import retrieval_escritorio, retrieval_usuario


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


def _mock_async_client(monkeypatch, payload: dict) -> AsyncMock:
    client = AsyncMock()
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    client.post.return_value = response
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(retrieval_module.httpx, "AsyncClient", MagicMock(return_value=cm))
    return client


async def test_retrieval_escritorio_usa_conversation_id_kb(monkeypatch) -> None:
    client = _mock_async_client(monkeypatch, {"results": [{"text": "regimento"}]})

    results = await retrieval_escritorio("tenant-1:5511999998888", "qual o regimento?")

    assert results == [{"text": "regimento"}]
    body = client.post.await_args.kwargs["json"]
    assert body["tenant_id"] == "tenant-1"
    assert body["conversation_id"] == "kb"
    assert body["message"] == "qual o regimento?"


async def test_retrieval_escritorio_inclui_doc_ids_quando_informado(monkeypatch) -> None:
    client = _mock_async_client(monkeypatch, {"results": []})

    await retrieval_escritorio("tenant-1:5511999998888", "regimento", doc_ids=["f1", "f2"])

    body = client.post.await_args.kwargs["json"]
    assert body["doc_ids"] == ["f1", "f2"]


async def test_retrieval_escritorio_sem_doc_ids_nao_inclui_chave(monkeypatch) -> None:
    client = _mock_async_client(monkeypatch, {"results": []})

    await retrieval_escritorio("tenant-1:5511999998888", "regimento")

    body = client.post.await_args.kwargs["json"]
    assert "doc_ids" not in body
