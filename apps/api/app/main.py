from fastapi import FastAPI

from app.api.v1.router import api_router

app = FastAPI(title="Advoxs API")

app.include_router(api_router)
