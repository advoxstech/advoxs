import os

from dotenv import load_dotenv

load_dotenv()

# Tenant reservado da base de conhecimento da plataforma (compartilhada entre
# todos os escritórios — legislação, jurisprudência etc. por categoria/base).
# Nunca usar como tenant_id de um escritório real.
SYSTEM_TENANT_ID = "system"

# Collection única — isolamento por tenant_id como payload indexado,
# filtro obrigatório na camada de acesso (clients/qdrant.py).
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "advoxs_kb")

# Dimensão do vetor denso (text-embedding-3-small = 1536).
DENSE_VECTOR_SIZE = int(os.getenv("DENSE_VECTOR_SIZE", "1536"))

# Ingestão em lotes — memória constante, qualquer que seja o tamanho do arquivo.
# Páginas de PDF lidas, divididas, vetorizadas e gravadas por vez.
INGEST_PAGE_BATCH = int(os.getenv("INGEST_PAGE_BATCH", "20"))
# Equivalente para DOCX/TXT (sem páginas): caracteres por lote.
INGEST_TEXT_BATCH_CHARS = int(os.getenv("INGEST_TEXT_BATCH_CHARS", "60000"))
# Trechos por chamada à API de vetores esparsos e à OpenAI; pontos por upsert no Qdrant.
SPARSE_BATCH = int(os.getenv("SPARSE_BATCH", "32"))
DENSE_BATCH = int(os.getenv("DENSE_BATCH", "64"))
QDRANT_UPSERT_BATCH = int(os.getenv("QDRANT_UPSERT_BATCH", "64"))
# PDFs acima disso são recusados com motivo claro, antes de qualquer processamento.
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "3000"))
# Documentos processados ao mesmo tempo neste processo; os demais pedidos esperam a vez.
INGEST_CONCURRENCY = int(os.getenv("INGEST_CONCURRENCY", "2"))
