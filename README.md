# Advoxs

Plataforma multi-tenant de atendimento jurídico por agentes de IA. Cada
escritório configura seus agentes, associa arquivos de base de conhecimento e
atende contatos pelo WhatsApp com alternância entre IA e atendimento humano.

## Estado funcional

| Situação | Escopo |
|---|---|
| **Implementado** | Cadastro e login, dashboard, conversas reais e de teste, agentes editáveis por tenant, base de conhecimento, takeover humano, Meta Cloud API e Z-API, créditos, cobrança do cliente final, perfil, onboarding e painel administrativo. |
| **Parcial** | Planos da própria plataforma, relatórios financeiros consolidados, recuperação automática de algumas falhas externas e cobertura de integração. |
| **Planejado** | Papéis além de `admin`, templates proativos de WhatsApp e as melhorias ainda abertas no backlog de 21/09/2026. |

## Arquitetura

| Serviço | Tecnologia | Função |
|---|---|---|
| `apps/web` | Next.js 15 | Painel do escritório e administração da plataforma. |
| `apps/api` | FastAPI / Python 3.12 | API principal, autenticação, dados multi-tenant, webhooks e billing. |
| `apps/worker` | Arq / Python 3.12 | Processamento assíncrono de mensagens e ingestão de arquivos. |
| `apps/agents` | FastAPI / Python 3.13 | LangGraph, tools, memória, geração de documentos e entrega no WhatsApp. |
| `apps/api_rag` | FastAPI / Python 3.13 | Ingestão e busca híbrida no Qdrant com filtro por tenant. |

PostgreSQL 16 armazena os dados da plataforma e os checkpoints; Redis 7 é
usado como fila, cache e debounce; Qdrant armazena os vetores da base de
conhecimento.

## Desenvolvimento local

Pré-requisitos: Node.js 20+, pnpm 9, `uv`, Docker e Docker Compose.

```bash
cp .env.example .env
pnpm install
docker compose up -d
pnpm dev
```

As migrations da API principal e do RAG podem ser aplicadas diretamente:

```bash
cd apps/api && uv sync --all-extras && uv run alembic upgrade head
cd ../api_rag && uv sync --all-extras && uv run alembic upgrade head
```

Não use os valores padrão de segredos do Compose em produção. Consulte
`.env.example` para a lista de variáveis; arquivos `.env` não devem ser
versionados.

## Validação

```bash
# Monorepo JavaScript
pnpm lint
pnpm test
pnpm build

# Execute em cada app Python aplicável
uv run ruff check .
uv run pytest
```

Na API principal, a suíte rápida fica em `apps/api/tests/unit`. O serviço de
agentes executa `tests/unit` por padrão; seus testes de integração dependentes
de serviços e modelo externo ficam fora da execução padrão.

## Documentação

- [Contexto técnico e convenções](CLAUDE.md)
- [Índice e política da documentação](docs/README.md)
- [API do serviço de agentes](apps/agents/API_AGENTS.md)
- [API do serviço de RAG](apps/api_rag/API.md)
- [Melhorias sugeridas pelo ChatGPT após a reunião de 21/09/2026](docs/sugestoes-chatgpt-2026-09-21.md)
