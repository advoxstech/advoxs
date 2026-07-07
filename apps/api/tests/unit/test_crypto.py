import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.crypto import decrypt_access_token, encrypt_access_token


def test_roundtrip(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_token_encryption_key", Fernet.generate_key().decode())

    encrypted = encrypt_access_token("meu-token-meta")

    assert encrypted != "meu-token-meta"
    assert decrypt_access_token(encrypted) == "meu-token-meta"


def test_sem_chave_configurada_levanta_erro(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_token_encryption_key", "")

    with pytest.raises(RuntimeError):
        encrypt_access_token("token")
