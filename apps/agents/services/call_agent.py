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

DATABASE_PASSWORD = os.getenv("POSTGRES_PASSWORD")
DATABASE_HOST = os.getenv("DATABASE_HOST")
DATABASE_PORT = os.getenv("DATABASE_PORT")
DB_URI = f"postgresql://postgres:{DATABASE_PASSWORD}@{DATABASE_HOST}:{DATABASE_PORT}/postgres"


async def run_agent(
    message: str,
    conversation_id: str,
    attachments: list = [],
    number_whatsapp: str | None = None,
    db_uri: str = DB_URI,
    num_before_messages: int = 35,
    extra_data: dict = {},
) -> list[str]:
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

    elapsed = round(time.perf_counter() - started_at, 3)
    logger.info(
        "Respostas geradas | conversation_id={} | total={} | elapsed_s={}",
        conversation_id,
        len(answers),
        elapsed,
    )
    for i, ans in enumerate(answers):
        logger.debug("Resposta {} | conversation_id={} | content={}", i + 1, conversation_id, ans)

    return answers
