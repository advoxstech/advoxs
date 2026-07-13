from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.billing import router as billing_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.credit_packages import router as credit_packages_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.end_customer_billing import router as end_customer_billing_router
from app.api.v1.knowledge_base import router as knowledge_base_router
from app.api.v1.platform_admin.auth import router as platform_admin_auth_router
from app.api.v1.platform_admin.dashboard import router as platform_admin_dashboard_router
from app.api.v1.platform_admin.playground import router as platform_admin_playground_router
from app.api.v1.platform_admin.tenants import router as platform_admin_tenants_router
from app.api.v1.profile import router as profile_router
from app.api.v1.signup import router as signup_router
from app.api.v1.webhooks.stripe import router as stripe_webhook_router
from app.api.v1.webhooks.whatsapp import router as whatsapp_webhook_router
from app.api.v1.whatsapp import router as whatsapp_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth_router)
api_router.include_router(billing_router)
api_router.include_router(conversations_router)
api_router.include_router(credit_packages_router)
api_router.include_router(dashboard_router)
api_router.include_router(end_customer_billing_router)
api_router.include_router(knowledge_base_router)
api_router.include_router(platform_admin_auth_router)
api_router.include_router(platform_admin_dashboard_router)
api_router.include_router(platform_admin_playground_router)
api_router.include_router(platform_admin_tenants_router)
api_router.include_router(profile_router)
api_router.include_router(signup_router)
api_router.include_router(stripe_webhook_router)
api_router.include_router(whatsapp_webhook_router)
api_router.include_router(whatsapp_router)


@api_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
