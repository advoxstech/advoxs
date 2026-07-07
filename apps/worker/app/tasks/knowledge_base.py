async def ingest_knowledge_base_file(ctx: dict, tenant_id: str, file_id: str) -> None:
    """Parsing -> chunking -> embedding -> indexacao no Qdrant, escopado por tenant_id."""
