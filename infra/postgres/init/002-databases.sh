#!/bin/bash
# Databases lógicos por serviço, na mesma instância:
#   - $POSTGRES_DB (advoxs)  -> api/worker (negócio, RLS)
#   - advoxs_agents          -> checkpoints do LangGraph (agents)
#   - advoxs_rag             -> metadados de documentos (api_rag)
# Cada serviço conecta com usuário próprio, sem acesso aos demais databases.
# Roda só na primeira criação do volume (docker-entrypoint-initdb.d).
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE advoxs_agents LOGIN PASSWORD '${AGENTS_DB_PASSWORD:-changeme}';
    CREATE DATABASE advoxs_agents OWNER advoxs_agents;

    CREATE ROLE advoxs_rag LOGIN PASSWORD '${RAG_DB_PASSWORD:-changeme}';
    CREATE DATABASE advoxs_rag OWNER advoxs_rag;

    -- Por padrão qualquer role conecta em qualquer database; cada serviço
    -- deve enxergar apenas o seu (dono conecta, PUBLIC não).
    REVOKE CONNECT ON DATABASE "$POSTGRES_DB" FROM PUBLIC;
    REVOKE CONNECT ON DATABASE advoxs_agents FROM PUBLIC;
    REVOKE CONNECT ON DATABASE advoxs_rag FROM PUBLIC;
EOSQL
