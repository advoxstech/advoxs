from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from constants import QDRANT_COLLECTION
from services.retrieval.main import RetrievalService


@pytest.fixture
def service():
    svc = RetrievalService()
    svc._transform_query = AsyncMock(return_value=("hyde doc", ["palavra", "chave"]))
    svc._embed_dense = AsyncMock(return_value=[0.1, 0.2])
    svc._embed_sparse = AsyncMock(return_value=([1, 2], [0.5, 0.7]))
    svc._qdrant = AsyncMock()
    return svc


def _hit(payload: dict, hit_id: str = "p1", score: float = 0.9):
    return SimpleNamespace(id=hit_id, score=score, payload=payload)


class TestSearchHybrid:
    async def test_propaga_tenant_e_extra_filters(self, service) -> None:
        service._qdrant.search.return_value = {
            "success": True,
            "data": SimpleNamespace(points=[]),
            "error": None,
        }

        await service.search_hybrid(
            query="pergunta", tenant_id="t1", extra_filters={"conversation_id": "c1"}
        )

        kwargs = service._qdrant.search.await_args.kwargs
        assert kwargs["collection_name"] == QDRANT_COLLECTION
        assert kwargs["tenant_id"] == "t1"
        assert kwargs["extra_filters"] == {"conversation_id": "c1"}

    async def test_le_texto_da_chave_text(self, service) -> None:
        service._qdrant.search.return_value = {
            "success": True,
            "data": SimpleNamespace(
                points=[_hit({"text": "conteúdo do chunk", "tenant_id": "t1", "name": "a.pdf"})]
            ),
            "error": None,
        }

        results = await service.search_hybrid(query="pergunta", tenant_id="t1")

        assert len(results) == 1
        assert results[0].text == "conteúdo do chunk"
        assert results[0].metadata == {"tenant_id": "t1", "name": "a.pdf"}

    async def test_sem_tenant_id_levanta_erro(self, service) -> None:
        with pytest.raises(ValueError, match="tenant_id"):
            await service.search_hybrid(query="pergunta", tenant_id="")

        service._qdrant.search.assert_not_awaited()

    async def test_falha_na_busca_retorna_lista_vazia(self, service) -> None:
        service._qdrant.search.return_value = {"success": False, "data": None, "error": "boom"}

        assert await service.search_hybrid(query="pergunta", tenant_id="t1") == []
