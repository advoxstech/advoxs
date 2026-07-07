from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.webhooks.whatsapp import router as whatsapp_webhook_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth_router)
api_router.include_router(conversations_router)
api_router.include_router(whatsapp_webhook_router)


@api_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
