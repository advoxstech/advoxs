import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.routes.documents.system import router_doc_system
from api.routes.documents.users import router_doc_users
from api.routes.health import router_health
from api.routes.retrievals import router_retrieval
from clients.qdrant import QdrantClient
from constants import DENSE_VECTOR_SIZE, QDRANT_COLLECTION
from database.models import Base
from database.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    UPLOAD_DIR_USER = os.getenv("UPLOAD_DIR_USER")
    os.makedirs(UPLOAD_DIR_USER, exist_ok=True)

    UPLOAD_DIR_SYSTEM = os.getenv("UPLOAD_DIR_SYSTEM")
    os.makedirs(UPLOAD_DIR_SYSTEM, exist_ok=True)

    # Conveniência para ambiente novo — em produção o schema é gerido pelo Alembic.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Provisiona a collection única (dense+sparse + índices de payload).
    await QdrantClient().ensure_collection(QDRANT_COLLECTION, DENSE_VECTOR_SIZE)

    yield


app = FastAPI(lifespan=lifespan)

app.include_router(router_doc_system)
app.include_router(router_doc_users)
app.include_router(router_health)
app.include_router(router_retrieval)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
