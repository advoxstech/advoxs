import os
import time
from dotenv import load_dotenv
from loguru import logger
from qdrant_client import AsyncQdrantClient as _AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    SparseVectorParams,
    NamedSparseVector,
    SparseVector,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    Prefetch,
    FusionQuery,
    Fusion,
)

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", None)


class QdrantClient:
    def __init__(self):
        self._url = QDRANT_URL
        self._client = _AsyncQdrantClient(url=self._url, api_key=QDRANT_API_KEY)
        logger.info(f"Conectado ao Qdrant em {self._url}")
        logger.info(f"API Key: {QDRANT_API_KEY}") 

    # ---------- CORE SAFE REQUEST ----------
    async def _safe_call(self, operation_name: str, fn, *args, **kwargs):
        """Wrapper que executa qualquer chamada ao Qdrant com logging e tratamento de erro uniforme."""
        started_at = time.perf_counter()
        try:
            logger.info(f"Operação Qdrant iniciada: {operation_name}")
            result = await fn(*args, **kwargs)
            elapsed = round(time.perf_counter() - started_at, 3)
            logger.info(
                f"Operação Qdrant concluída: {operation_name}",
                operation=operation_name,
                elapsed_s=elapsed,
            )
            return {
                "success": True,
                "data": result,
                "error": None,
            }

        except Exception as e:
            elapsed = round(time.perf_counter() - started_at, 3)
            logger.error(
                f"Erro na operação Qdrant [{operation_name}]: {str(e)}",
                operation=operation_name,
                elapsed_s=elapsed,
            )
            return {
                "success": False,
                "data": None,
                "error": str(e),
            }


    # ---------- POINTS ----------
    async def upsert_points(self, collection_name: str, points: list[PointStruct]):
        
        return await self._safe_call(
            "upsert_points",
            self._client.upsert,
            collection_name=collection_name,
            points=points,
        )

    async def search(
        self,
        collection_name: str,
        dense_vector: list[float],
        sparse_indices: list[int],
        sparse_values: list[float],
        top_k: int = 5,
        prefetch_k: int = 20,
        payload_filter: dict | None = None,
        dense_vector_name: str = "dense",
        sparse_vector_name: str = "sparse",
    ):
        """Busca híbrida: Dense (ANN) + Sparse (BM25/SPLADE) fundidos via RRF.

        Args:
            dense_vector: Embedding denso da query.
            sparse_indices: Índices dos tokens do vetor esparso (BM25/SPLADE).
            sparse_values: Pesos correspondentes aos índices esparsos.
            top_k: Número de resultados finais após fusão RRF.
            prefetch_k: Candidatos pré-buscados por cada ramo antes da fusão.
            payload_filter: Filtro opcional aplicado em ambos os ramos.
            dense_vector_name: Nome do vetor denso na coleção (padrão: "dense").
            sparse_vector_name: Nome do vetor esparso na coleção (padrão: "sparse").
        """


        list_conditions = []
        for key, value in payload_filter.items():
            list_conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
            
        if list_conditions:
            payload_filter = Filter(must=list_conditions)


        prefetches = [
            # Ramo denso — busca por similaridade semântica
            Prefetch(
                query=dense_vector,
                using=dense_vector_name,
                limit=prefetch_k,
                filter=payload_filter,
            ),
            # Ramo esparso — busca lexical (BM25/SPLADE)
            Prefetch(
                query=SparseVector(
                    indices=sparse_indices,
                    values=sparse_values,
                ),
                using=sparse_vector_name,
                limit=prefetch_k,
                filter=payload_filter,
            ),
        ]

        
        return await self._safe_call(
            "hybrid_search",
            self._client.query_points,
            collection_name=collection_name,
            prefetch=prefetches,
            query=FusionQuery(fusion=Fusion.RRF),
            limit=top_k,
            with_payload=True,
        )

    async def get_point(self, collection_name: str, point_id: str | int):
        return await self._safe_call(
            "get_point",
            self._client.retrieve,
            collection_name=collection_name,
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )

    async def delete_points_by_filter(self, collection_name: str, field: str, value: str):
        payload_filter = Filter(
            must=[FieldCondition(key=field, match=MatchValue(value=value))]
        )
        return await self._safe_call(
            "delete_points_by_filter",
            self._client.delete,
            collection_name=collection_name,
            points_selector=payload_filter,
        )
    

    # ---------- TESTE ----------
    async def test_connection(self):
        """Testa a conexão e operações básicas do Qdrant (upsert → hybrid search → delete)."""
        TEST_COLLECTION = "_test_connection"
        VECTOR_SIZE = 4
        logger.info("Iniciando teste de conexão com o Qdrant")

        # 1. Cria coleção temporária com vetores denso + esparso
        result = await self.create_collection(TEST_COLLECTION, vector_size=VECTOR_SIZE)
        if not result["success"]:
            logger.error(f"Falha ao criar coleção de teste: {result['error']}")
            return result

        # 2. Insere um ponto de teste com vetor denso e esparso
        test_point = PointStruct(
            id=1,
            vector={
                "dense": [0.1, 0.2, 0.3, 0.4],
                "sparse": SparseVector(indices=[0, 3, 7], values=[0.9, 0.5, 0.3]),
            },
            payload={"test": True, "label": "ping"},
        )
        result = await self.upsert_points(TEST_COLLECTION, [test_point])
        if not result["success"]:
            logger.error(f"Falha ao inserir ponto de teste: {result['error']}")
            await self.delete_collection(TEST_COLLECTION)
            return result

        # 3. Busca híbrida com os mesmos vetores
        result = await self.search(
            TEST_COLLECTION,
            dense_vector=[0.1, 0.2, 0.3, 0.4],
            sparse_indices=[0, 3, 7],
            sparse_values=[0.9, 0.5, 0.3],
            top_k=1,
        )
        if not result["success"]:
            logger.error(f"Falha na busca de teste: {result['error']}")
            await self.delete_collection(TEST_COLLECTION)
            return result

        hits = result["data"].points
        logger.info(f"Busca retornou {len(hits)} resultado(s)")

        # 4. Limpa coleção temporária
        await self.delete_collection(TEST_COLLECTION)

        if hits:
            logger.info("✅ Teste de conexão com Qdrant concluído com sucesso")
            return {"success": True, "data": "Conexão OK", "error": None}
        else:
            logger.warning("⚠️ Conexão OK, mas nenhum resultado retornado na busca híbrida")
            return {"success": False, "data": None, "error": "Nenhum hit retornado"}


if __name__ == "__main__":
    import asyncio

    async def main():
        client = QdrantClient()
        result = await client.test_connection()
        print(result)

    asyncio.run(main())