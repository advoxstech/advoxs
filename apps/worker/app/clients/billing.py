"""Chama o endpoint interno de checkout do cliente final (apps/api) direto do
worker — único chamador desse endpoint (o mecanismo antigo, embutido no
agents, foi removido). Autenticado pela mesma INTERNAL_SERVICE_KEY (ver
apps/api/app/api/internal_deps.py)."""

import httpx

from app.config import settings


class BillingCheckoutError(Exception):
    pass


async def create_end_customer_checkout(
    tenant_id: str, contact_phone_number: str, package_id: str
) -> str:
    headers = (
        {"Authorization": settings.internal_service_key} if settings.internal_service_key else {}
    )
    try:
        async with httpx.AsyncClient(base_url=settings.api_url, timeout=15) as client:
            response = await client.post(
                "/api/v1/internal/end-customer-billing/checkout",
                json={
                    "tenant_id": tenant_id,
                    "contact_phone_number": contact_phone_number,
                    "package_id": package_id,
                },
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise BillingCheckoutError(f"Falha de rede ao gerar o link de pagamento: {exc}") from exc

    if response.is_error:
        raise BillingCheckoutError(
            f"Falha ao gerar o link de pagamento — HTTP {response.status_code}: {response.text}"
        )
    return response.json()["checkout_url"]
