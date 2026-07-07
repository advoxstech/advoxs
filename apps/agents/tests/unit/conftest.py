import os

# Envs mínimas para importar os módulos em testes unitários (LLM sempre mockado).
# Só nos unit: os testes de integração usam a chave real do .env.
os.environ.setdefault("OPENAI_API_KEY", "test-key")
