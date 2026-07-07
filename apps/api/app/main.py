from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.queue import close_arq_pool
from app.core.redis import close_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_arq_pool()
    await close_redis()


app = FastAPI(title="Advoxs API", lifespan=lifespan)

app.include_router(api_router)
