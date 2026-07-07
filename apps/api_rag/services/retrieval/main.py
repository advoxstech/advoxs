import json
import os
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv
from langchain.messages import HumanMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from loguru import logger

from clients.qdrant import QdrantClient
from constants import QDRANT_COLLECTION

load_dotenv()

# ---------- CONFIG ----------

TOP_K = int(os.getenv("TOP_K", "5"))
PREFETCH_K = int(os.getenv("PREFETCH_K", "20"))
DENSE_MODEL = os.getenv("DENSE_MODEL")
CHAT_MODEL = os.getenv("CHAT_MODEL")
URL_API_LOCAL_SPARSE = os.getenv("URL_API_LOCAL_SPARSE")


# ---------- SCHEMAS ----------


@dataclass
class RetrievalResult:
    chunk_id: str
    score: float
    text: str
    metadata: dict


# ---------- SERVIÇO ----------


class RetrievalService:
    """Pipeline completo de retrieval híbrido (dense + sparse).

    Técnicas aplicadas:
      - HyDE (Hypothetical Document Embedding) → vetor denso
      - Extração de palavras-chave via LLM       → vetor esparso

    Todo retrieval é escopado por tenant_id (obrigatório) — a base da
    plataforma usa o tenant reservado SYSTEM_TENANT_ID.
    """

    def __init__(
        self,
        top_k: int = TOP_K,
        prefetch_k: int = PREFETCH_K,
        dense_model: str = DENSE_MODEL,
        chat_model: str = CHAT_MODEL,
        sparse_api_url: str = URL_API_LOCAL_SPARSE,
    ):
        self.top_k = top_k
        self.prefetch_k = prefetch_k
        self.sparse_api_url = sparse_api_url

        self._emb_model = OpenAIEmbeddings(model=dense_model)
        self._openai = ChatOpenAI(model=chat_model)
        self._qdrant = QdrantClient()

    # ------------------------------------------------------------------ #
    # Ponto de entrada público                                             #
    # ------------------------------------------------------------------ #

    async def search_hybrid(
        self,
        query: str,
        tenant_id: str,
        extra_filters: dict | None = None,
    ) -> list[RetrievalResult]:
        """Pipeline completo de retrieval híbrido.

        Fluxo:
            query bruta
                ↓
            LLM transforma → hyde_doc (denso) + keywords (esparso)
                ↓
            embeddings gerados para cada ramo
                ↓
            busca híbrida no Qdrant (dense + sparse via RRF),
            sempre filtrada por tenant_id (+ extra_filters)
                ↓
            lista de RetrievalResult ordenada por score
        """
        if not tenant_id:
            raise ValueError("tenant_id é obrigatório no retrieval")

        hyde_doc, keywords = await self._transform_query(query)

        logger.info("Gerando embeddings...")
        dense_vector = await self._embed_dense(hyde_doc)

        logger.info("Gerando embeddings (sparse)...")
        sparse_indices, sparse_values = await self._embed_sparse(text=" ".join(keywords))

        logger.info(
            f"Embeddings gerados | dense={len(dense_vector)}"
            f" | sparse={len(sparse_indices)} tokens ativos"
        )

        result = await self._qdrant.search(
            collection_name=QDRANT_COLLECTION,
            tenant_id=tenant_id,
            dense_vector=dense_vector,
            sparse_indices=sparse_indices,
            sparse_values=sparse_values,
            top_k=self.top_k,
            prefetch_k=self.prefetch_k,
            extra_filters=extra_filters,
        )

        if not result["success"]:
            logger.error(f"Falha na busca híbrida: {result['error']}")
            return []

        hits = result["data"].points
        logger.info(f"Busca retornou {len(hits)} chunk(s)")

        return [
            RetrievalResult(
                chunk_id=str(hit.id),
                score=hit.score,
                text=hit.payload.get("text", ""),
                metadata={k: v for k, v in hit.payload.items() if k != "text"},
            )
            for hit in hits
        ]

    # ------------------------------------------------------------------ #
    # Métodos privados                                                     #
    # ------------------------------------------------------------------ #

    async def _transform_query(self, query: str) -> tuple[str, list[str]]:
        """Gera hyde_doc e keywords via LLM."""
        prompt = f"""Você é um especialista em recuperação de informação.

Dada a pergunta do usuário abaixo, gere um JSON com dois campos:

1. "hyde": Um parágrafo curto (3-5 frases) que seria um trecho ideal de documento
   respondendo diretamente a essa pergunta. Escreva como se fosse um documento real,
   não como resposta direta.

2. "keywords": Uma lista de 5 a 10 termos e expressões-chave extraídos da pergunta,
   incluindo sinônimos e variações relevantes.

Responda APENAS com o JSON, sem texto adicional, sem markdown.

Pergunta: {query}

Exemplo de saída:
{{
  "hyde": "A autenticação JWT funciona através de tokens assinados...",
  "keywords": ["JWT", "autenticação", "token", "assinatura", "bearer"]
}}"""

        logger.info(f"Transformando query via LLM: '{query}'")
        response = await self._openai.ainvoke([HumanMessage(content=prompt)])

        try:
            parsed = json.loads(response.content)
            hyde_doc = parsed["hyde"]
            keywords = parsed["keywords"]
            logger.info(f"HyDE gerado ({len(hyde_doc)} chars) | Keywords: {keywords}")
            return hyde_doc, keywords
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Falha ao parsear resposta do LLM: {e}. Usando fallback.")
            return query, query.split()

    async def _embed_dense(self, text: str) -> list[float]:
        """Gera vetor denso via OpenAI Embeddings."""
        embeddings = await self._emb_model.aembed_query(text)
        logger.debug(f"Dense embedding gerado | dims={len(embeddings)}")
        return embeddings

    async def _embed_sparse(self, text: str) -> tuple[list[int], list[float]]:
        """Gera vetor esparso via API local (assíncrono)."""
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                self.sparse_api_url,
                json={"document_id": "query", "texts": [text]},
            )
            response.raise_for_status()

        vector = response.json()["vectors"][0]
        indices = vector["indices"]
        values = vector["values"]

        logger.debug(f"Sparse embedding gerado | tokens ativos={len(indices)}")
        return indices, values
