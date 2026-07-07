from arq.connections import RedisSettings

from app.config import settings
from app.tasks.knowledge_base import ingest_knowledge_base_file
from app.tasks.messages import process_inbound_message


class WorkerSettings:
    functions = [ingest_knowledge_base_file, process_inbound_message]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
