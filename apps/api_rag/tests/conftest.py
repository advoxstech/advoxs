import os

# Envs mínimas para importar a aplicação em testes unitários (sem serviços reais).
# setdefault roda antes dos imports dos módulos, então vence o load_dotenv().
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("QDRANT_COLLECTION", "test_kb")
os.environ.setdefault("DENSE_MODEL", "text-embedding-3-small")
os.environ.setdefault("CHAT_MODEL", "gpt-5-mini")
os.environ.setdefault("URL_API_LOCAL_SPARSE", "http://localhost:9999/embed")
os.environ.setdefault("UPLOAD_DIR_USER", "/tmp/api_rag_test/users")
os.environ.setdefault("UPLOAD_DIR_SYSTEM", "/tmp/api_rag_test/system")
