"""
Teste manual do grafo completo.
"""
import asyncio
import os
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from agents.workflow import graph
from dotenv import load_dotenv

load_dotenv()

DATABASE_PASSWORD = os.getenv("POSTGRES_PASSWORD")
DATABASE_HOST = os.getenv("DATABASE_HOST")
DATABASE_PORT = os.getenv("DATABASE_PORT")
DB_URI = f"postgresql://postgres:{DATABASE_PASSWORD}@{DATABASE_HOST}:{DATABASE_PORT}/postgres"

# ══════════════════════════════════════════════
# VARIÁVEIS — edite aqui antes de rodar
# ══════════════════════════════════════════════

MENSAGEM = "rpocure na base sobre esses documentos"

CONVERSATION_ID = "teste_manual_2"

# None → começa na secretaria
# "agente_condominial" | "agente_contratos" | "agente_direito_consumidor"
SPECIALIST = "agente_condominial"

NUM_BEFORE_MESSAGES = 40

# ══════════════════════════════════════════════


def _print_historico(messages: list, prior_count: int) -> None:
    print("\n" + "=" * 60)
    print(f"HISTÓRICO ({len(messages)} mensagens | {prior_count} anteriores ao invoke)")
    print("=" * 60)

    for i, m in enumerate(messages):
        prefixo = "  [hist]" if i < prior_count else "  [novo]"
        if m.type == "human":
            print(f"{prefixo} usuário : {m.content}")
        elif m.type == "ai" and m.content:
            print(f"{prefixo} agente  : {m.content}")
        elif m.type == "ai" and m.tool_calls:
            nomes = ", ".join(tc["name"] for tc in m.tool_calls)
            print(f"{prefixo} tool▶   : {nomes}")
        elif m.type == "tool":
            preview = (m.content or "(vazio)")
            print(f"{prefixo} ◀result : {preview}")

    print("=" * 60)


async def _run():
    config = {"configurable": {"thread_id": CONVERSATION_ID}}

    async with AsyncPostgresSaver.from_conn_string(DB_URI) as checkpointer:
        await checkpointer.setup()
        agent = graph.compile(checkpointer=checkpointer)

        prior_state = await agent.aget_state(config)
        prior_count = len(prior_state.values.get("messages", [])) if prior_state.values else 0

        response = await agent.ainvoke(
            {
                "messages": [HumanMessage(content=MENSAGEM)],
                "attachments": [],
                "conversation_id": CONVERSATION_ID,
                "num_before_messages": NUM_BEFORE_MESSAGES,
                "current_specialist": SPECIALIST,
                "receptive_message_specialist": False,
            },
            config=config,
        )

    print(f"\nSPECIALIST INICIAL: {SPECIALIST}")
    print(f"SPECIALIST FINAL:   {response.get('current_specialist')}")
    _print_historico(response["messages"], prior_count)
    return response


def test_agente():
    asyncio.run(_run())




if __name__ == "__main__":
    test_agente()