# RAG service

Microserviço interno de ingestão de PDF, DOCX e TXT e de busca híbrida no
Qdrant. Todos os documentos e consultas de escritórios são filtrados por
`tenant_id`; a base compartilhada usa o tenant reservado `system`.

A referência de endpoints, payloads, autenticação, modelo de dados e ressalvas
está em [API.md](API.md).

```bash
uv sync --all-extras
uv run alembic upgrade head
uv run pytest
uv run ruff check .
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

O serviço depende de PostgreSQL, Qdrant, OpenAI e da API de embeddings
esparsos. Suas rotas de documentos e retrieval são internas e exigem
`Authorization: <API_KEY>`.
