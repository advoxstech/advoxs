import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.crypto import (
    decrypt_access_token,
    decrypt_tenant_secret,
    encrypt_access_token,
    encrypt_tenant_secret,
)


def test_roundtrip(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_token_encryption_key", Fernet.generate_key().decode())

    encrypted = encrypt_access_token("meu-token-meta")

    assert encrypted != "meu-token-meta"
    assert decrypt_access_token(encrypted) == "meu-token-meta"


def test_sem_chave_configurada_levanta_erro(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_token_encryption_key", "")

    with pytest.raises(RuntimeError):
        encrypt_access_token("token")


def test_tenant_secret_roundtrip(monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "tenant_stripe_key_encryption_key", key)

    encrypted = encrypt_tenant_secret("sk_test_do_tenant")

    assert encrypted != "sk_test_do_tenant"
    assert decrypt_tenant_secret(encrypted) == "sk_test_do_tenant"


def test_tenant_secret_sem_chave_configurada_levanta_erro(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tenant_stripe_key_encryption_key", "")

    with pytest.raises(RuntimeError):
        encrypt_tenant_secret("sk_test")
