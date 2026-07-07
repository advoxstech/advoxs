from fastapi import APIRouter

from app.api.v1.webhooks.whatsapp import router as whatsapp_webhook_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(whatsapp_webhook_router)


@api_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
