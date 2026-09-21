# Agents service

Microserviço interno que executa os agentes dinâmicos de cada tenant com
LangGraph, mantém o histórico em checkpoints do PostgreSQL e entrega respostas
por Meta Cloud API ou Z-API.

A referência de arquitetura, contratos HTTP, autenticação, estado, tools e
limitações está em [API_AGENTS.md](API_AGENTS.md).

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run uvicorn main:app --host 0.0.0.0 --port 8001
```

O serviço depende de PostgreSQL, Redis, OpenAI e `api_rag`. As rotas internas
autenticadas usam `Authorization: <AGENTS_API_KEY>`; a ausência dessa variável
só deve ser aceita no desenvolvimento local.
