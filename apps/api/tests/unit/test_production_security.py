import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.internal_deps import verify_internal_service_key
from app.api.v1.webhooks.whatsapp import _verify_signature
from app.core.config import settings
from app.main import app


@pytest.mark.parametrize(
    "flag,path,method",
    [
        ("meta_enabled", "/api/v1/webhooks/whatsapp", "post"),
        ("meta_enabled", "/api/v1/webhooks/whatsapp", "get"),
        ("meta_enabled", "/api/v1/whatsapp/connect", "post"),
        ("stripe_enabled", "/api/v1/webhooks/stripe", "post"),
        ("stripe_enabled", "/api/v1/signup/checkout", "post"),
        ("stripe_enabled", "/api/v1/billing/checkout", "post"),
        ("stripe_connect_enabled", "/api/v1/webhooks/stripe/connect", "post"),
        ("stripe_connect_enabled", "/api/v1/end-customer-billing/connect-account", "post"),
    ],
)
def test_disabled_integration_rejects_before_processing(monkeypatch, flag, path, method):
    monkeypatch.setattr(settings, flag, False)
    with TestClient(app) as client:
        response = getattr(client, method)(path)
    assert response.status_code == 503
    assert "desabilitada" in response.json()["detail"]


async def test_missing_internal_key_cannot_bypass_auth_in_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "internal_service_key", "")
    with pytest.raises(HTTPException) as exc:
        await verify_internal_service_key(None)
    assert exc.value.status_code == 503


async def test_valid_internal_config_still_rejects_missing_or_wrong_header(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_key", "configured-key")
    for header in (None, "wrong-key"):
        with pytest.raises(HTTPException) as exc:
            await verify_internal_service_key(header)
        assert exc.value.status_code == 403
    await verify_internal_service_key("configured-key")


def test_missing_meta_secret_cannot_bypass_signature_in_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "meta_app_secret", "")
    with pytest.raises(HTTPException) as exc:
        _verify_signature(b"{}", None)
    assert exc.value.status_code == 503
