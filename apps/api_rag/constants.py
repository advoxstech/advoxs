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
