"""Cria o link de pagamento chamando o endpoint interno do api — a secret
key da Stripe do tenant nunca chega até o agents, só a URL final."""

import os

import httpx
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

API_URL = os.getenv("API_URL", "http://api:8000")
INTERNAL_SERVICE_KEY = os.getenv("INTERNAL_SERVICE_KEY", "")


async def criar_link_pagamento(
    tenant_id: str, contact_phone_number: str, package_id: str
) -> str | None:
    headers = {"Authorization": INTERNAL_SERVICE_KEY} if INTERNAL_SERVICE_KEY else {}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{API_URL}/api/v1/internal/end-customer-billing/checkout",
                json={
                    "tenant_id": tenant_id,
                    "contact_phone_number": contact_phone_number,
                    "package_id": package_id,
                },
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()["checkout_url"]
    except httpx.HTTPStatusError as e:
        logger.error(
            "Erro HTTP ao criar link de pagamento | status={} | response={}",
            e.response.status_code,
            e.response.text,
        )
        return None
    except Exception as e:
        logger.error("Erro ao criar link de pagamento | error={}", str(e))
        return None
