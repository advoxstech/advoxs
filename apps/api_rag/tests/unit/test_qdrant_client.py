from unittest.mock import AsyncMock

import pytest
from qdrant_client.models import PointStruct

from clients.qdrant import QdrantClient, _tenant_filter


class TestTenantFilter:
    def test_exige_tenant_id(self) -> None:
        with pytest.raises(ValueError, match="tenant_id"):
            _tenant_filter("")

    def test_tenant_sempre_presente(self) -> None:
        f = _tenant_filter("t1")

        assert len(f.must) == 1
        assert f.must[0].key == "tenant_id"
        assert f.must[0].match.value == "t1"

    def test_extra_filters_somam_ao_tenant(self) -> None:
        f = _tenant_filter("t1", {"conversation_id": "c1", "base": "condominial"})

        keys = {c.key for c in f.must}
        assert keys == {"tenant_id", "conversation_id", "base"}


class TestSearch:
    async def test_filtro_de_tenant_aplicado_nos_dois_ramos(self) -> None:
        client = QdrantClient()
        client._client.query_points = AsyncMock(return_value="ok")

        result = await client.search(
            collection_name="kb",
            tenant_id="t1",
            dense_vector=[0.1, 0.2],
            sparse_indices=[1],
            sparse_values=[0.5],
            extra_filters={"base": "contratos"},
        )

        assert result["success"] is True
        prefetches = client._client.query_points.await_args.kwargs["prefetch"]
        assert len(prefetches) == 2
        for prefetch in prefetches:
            keys = {c.key for c in prefetch.filter.must}
            assert "tenant_id" in keys
            assert "base" in keys

    async def test_sem_tenant_id_levanta_erro(self) -> None:
        client = QdrantClient()
        client._client.query_points = AsyncMock()

        with pytest.raises(ValueError, match="tenant_id"):
            await client.search(
                collection_name="kb",
                tenant_id="",
                dense_vector=[0.1],
                sparse_indices=[1],
                sparse_values=[0.5],
            )

        client._client.query_points.assert_not_awaited()


class TestUpsert:
    async def test_rejeita_ponto_sem_tenant_id(self) -> None:
        client = QdrantClient()
        client._client.upsert = AsyncMock()
        point = PointStruct(id="1", vector={"dense": [0.1]}, payload={"text": "x"})

        with pytest.raises(ValueError, match="tenant_id"):
            await client.upsert_points("kb", [point])

        client._client.upsert.assert_not_awaited()

    async def test_aceita_ponto_com_tenant_id(self) -> None:
        client = QdrantClient()
        client._client.upsert = AsyncMock(return_value="ok")
        point = PointStruct(
            id="1", vector={"dense": [0.1]}, payload={"text": "x", "tenant_id": "t1"}
        )

        result = await client.upsert_points("kb", [point])

        assert result["success"] is True
        client._client.upsert.assert_awaited_once()


class TestDelete:
    async def test_delete_sempre_filtra_por_tenant(self) -> None:
        client = QdrantClient()
        client._client.delete = AsyncMock(return_value="ok")

        await client.delete_points_by_filter("kb", tenant_id="t1", field="doc_id", value="d1")

        selector = client._client.delete.await_args.kwargs["points_selector"]
        conditions = {c.key: c.match.value for c in selector.must}
        assert conditions == {"tenant_id": "t1", "doc_id": "d1"}

    async def test_delete_sem_tenant_levanta_erro(self) -> None:
        client = QdrantClient()
        client._client.delete = AsyncMock()

        with pytest.raises(ValueError, match="tenant_id"):
            await client.delete_points_by_filter("kb", tenant_id="", field="doc_id", value="d1")
