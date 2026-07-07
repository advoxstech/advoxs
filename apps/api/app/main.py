from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.queue import close_arq_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_arq_pool()


app = FastAPI(title="Advoxs API", lifespan=lifespan)

app.include_router(api_router)
