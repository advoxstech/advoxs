from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from loguru import logger

from agents.workflow import graph
from services.call_agent import DB_URI

# contact = o cliente final (HumanMessage); attendant = o atendente do
# escritório falando pelo "nosso lado" (AIMessage) — assim, quando a IA
# reassume, o histórico dela reflete quem disse o quê.
ROLE_TO_MESSAGE = {"contact": HumanMessage, "attendant": AIMessage}


async def add_context_messages(
    thread_id: str, messages: list[dict], db_uri: str = DB_URI
) -> int:
    """Anexa mensagens ao checkpoint sem rodar o grafo (sem LLM, sem débito).

    Mantém a memória do agente durante o takeover humano — aupdate_state usa
    o reducer add_messages do estado, só acrescentando ao histórico.
    """
    lc_messages = [ROLE_TO_MESSAGE[m["role"]](content=m["content"]) for m in messages]
    config = {"configurable": {"thread_id": thread_id}}
    async with AsyncPostgresSaver.from_conn_string(db_uri) as checkpointer:
        await checkpointer.setup()
        agent = graph.compile(checkpointer=checkpointer)
        await agent.aupdate_state(config, {"messages": lc_messages})
    logger.info(
        "Contexto anexado ao checkpoint | thread_id={} | mensagens={}",
        thread_id,
        len(lc_messages),
    )
    return len(lc_messages)
