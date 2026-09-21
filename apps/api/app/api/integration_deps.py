"""Explicitly disabled integrations reject requests before any side effects."""

from fastapi import HTTPException

from app.core.config import settings


def require_meta() -> None:
    if not settings.meta_enabled:
        raise HTTPException(status_code=503, detail="Integração Meta desabilitada")


def require_stripe() -> None:
    if not settings.stripe_enabled:
        raise HTTPException(status_code=503, detail="Integração Stripe desabilitada")


def require_stripe_connect() -> None:
    if not settings.stripe_connect_enabled:
        raise HTTPException(status_code=503, detail="Integração Stripe Connect desabilitada")
