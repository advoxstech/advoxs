import asyncio
import os
import time

from dotenv import load_dotenv
from loguru import logger
from qdrant_client import AsyncQdrantClient as _AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    Prefetch,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", None)

# Campos de payload indexados — todo filtro passa por eles.
INDEXED_PAYLOAD_FIELDS = ("tenant_id", "base", "conversation_id", "doc_id")


def _tenant_filter(tenant_id: str, extra_filters: dict | None = None) -> Filter:
    """Monta o filtro com tenant_id obrigatório + condições extras.

    O tenant_id nunca é opcional nem decisão do chamador de alto nível
    (agente): sem ele, qualquer operação de busca/deleção falha aqui.
    """
    if not tenant_id:
        raise ValueError("tenant_id é obrigatório em todo acesso ao Qdrant")

    conditions = [FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]
    for key, value in (extra_filters or {}).items():
        conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
    return Filter(must=conditions)


class QdrantClient:
    def __init__(self):
        self._url = QDRANT_URL
        self._client = _AsyncQdrantClient(url=self._url, api_key=QDRANT_API_KEY)
        logger.info(f"Conectado ao Qdrant em {self._url}")

    # ---------- CORE SAFE REQUEST ----------
    async def _safe_call(self, operation_name: str, fn, *args, **kwargs):
        """Executa qualquer chamada ao Qdrant com logging e tratamento de erro uniforme."""
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

    # ---------- COLLECTION ----------
    async def ensure_collection(
        self, collection_name: str, dense_vector_size: int, retries: int = 5
    ):
        """Cria a collection (vetores nomeados dense+sparse) e os índices de
        payload se ainda não existirem. Idempotente; chamado no startup.

        Faz retry porque no docker-compose o Qdrant pode subir depois da API.
        """
        for attempt in range(1, retries + 1):
            try:
                if not await self._client.collection_exists(collection_name):
                    await self._client.create_collection(
                        collection_name=collection_name,
                        vectors_config={
                            "dense": VectorParams(size=dense_vector_size, distance=Distance.COSINE)
                        },
                        sparse_vectors_config={"sparse": SparseVectorParams()},
                    )
                    logger.info(f"Collection '{collection_name}' criada")

                for field in INDEXED_PAYLOAD_FIELDS:
                    await self._client.create_payload_index(
                        collection_name=collection_name,
                        field_name=field,
                        field_schema=PayloadSchemaType.KEYWORD,
                    )
                logger.info(f"Collection '{collection_name}' pronta (índices ok)")
                return
            except Exception as e:
                logger.warning(
                    f"Tentativa {attempt}/{retries} de provisionar '{collection_name}' falhou: {e}"
                )
                if attempt == retries:
                    raise
                await asyncio.sleep(2)

    # ---------- POINTS ----------
    async def upsert_points(self, collection_name: str, points: list[PointStruct]):
        for point in points:
            if not (point.payload or {}).get("tenant_id"):
                raise ValueError("Todo ponto inserido no Qdrant precisa de tenant_id no payload")

        return await self._safe_call(
            "upsert_points",
            self._client.upsert,
            collection_name=collection_name,
            points=points,
        )

    async def search(
        self,
        collection_name: str,
        tenant_id: str,
        dense_vector: list[float],
        sparse_indices: list[int],
        sparse_values: list[float],
        top_k: int = 5,
        prefetch_k: int = 20,
        extra_filters: dict | None = None,
        dense_vector_name: str = "dense",
        sparse_vector_name: str = "sparse",
    ):
        """Busca híbrida: Dense (ANN) + Sparse (BM25/SPLADE) fundidos via RRF.

        O filtro por tenant_id é obrigatório e aplicado em ambos os ramos;
        extra_filters adiciona condições (ex: base, conversation_id).
        """
        payload_filter = _tenant_filter(tenant_id, extra_filters)

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

    async def delete_points_by_filter(
        self, collection_name: str, tenant_id: str, field: str, value: str
    ):
        payload_filter = _tenant_filter(tenant_id, {field: value})
        return await self._safe_call(
            "delete_points_by_filter",
            self._client.delete,
            collection_name=collection_name,
            points_selector=payload_filter,
        )
