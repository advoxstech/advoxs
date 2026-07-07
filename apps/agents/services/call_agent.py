from agents.workflow import graph
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv
from loguru import logger
import os
import time
from langfuse.langchain import CallbackHandler
load_dotenv()

langfuse_handler = CallbackHandler()

# Database dedicado ao checkpoint do LangGraph (advoxs_agents no monorepo);
# defaults mantêm compatibilidade com o compose standalone legado.
DATABASE_USER = os.getenv("DATABASE_USER", "postgres")
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD") or os.getenv("POSTGRES_PASSWORD")
DATABASE_HOST = os.getenv("DATABASE_HOST")
DATABASE_PORT = os.getenv("DATABASE_PORT")
DATABASE_NAME = os.getenv("DATABASE_NAME", "postgres")
DB_URI = (
    f"postgresql://{DATABASE_USER}:{DATABASE_PASSWORD}"
    f"@{DATABASE_HOST}:{DATABASE_PORT}/{DATABASE_NAME}"
)


def sum_usage_tokens(messages: list) -> int:
    """Soma os tokens (input+output) das mensagens de IA de uma execução.

    O usage_metadata é preenchido pelo langchain-openai em cada AIMessage —
    inclui as chamadas intermediárias com tool_calls, que também custam tokens.
    """
    total = 0
    for m in messages:
        usage = getattr(m, "usage_metadata", None)
        if m.type == "ai" and usage:
            total += usage.get("total_tokens", 0)
    return total


async def run_agent(
    message: str,
    conversation_id: str,
    attachments: list = [],
    number_whatsapp: str | None = None,
    db_uri: str = DB_URI,
    num_before_messages: int = 35,
    extra_data: dict = {},
) -> tuple[list[str], int]:
    started_at = time.perf_counter()
    config = {"configurable": {"thread_id": conversation_id}, "callbacks": [langfuse_handler]}

    logger.info(
        "Preparando agente | conversation_id={} | num_before_messages={} | has_whatsapp={}",
        conversation_id,
        num_before_messages,
        bool(number_whatsapp),
    )

    async with AsyncPostgresSaver.from_conn_string(db_uri) as checkpointer:
        await checkpointer.setup()
        agent = graph.compile(checkpointer=checkpointer)

        prior_state = await agent.aget_state(config)
        prior_count = len(prior_state.values.get("messages", [])) if prior_state.values else 0

        logger.info("Enviando mensagem ao agente | conversation_id={}", conversation_id)
        response = await agent.ainvoke(
            {
                "messages": [HumanMessage(content=message)],
                "attachments": attachments,
                "conversation_id": conversation_id,
                "num_before_messages": num_before_messages,
            },
            config=config,
        )

    new_messages = response["messages"][prior_count:]
    answers = [m.content for m in new_messages if m.type == "ai" and m.content]
    tokens_used = sum_usage_tokens(new_messages)

    elapsed = round(time.perf_counter() - started_at, 3)
    logger.info(
        "Respostas geradas | conversation_id={} | total={} | tokens={} | elapsed_s={}",
        conversation_id,
        len(answers),
        tokens_used,
        elapsed,
    )
    for i, ans in enumerate(answers):
        logger.debug("Resposta {} | conversation_id={} | content={}", i + 1, conversation_id, ans)

    return answers, tokens_used
