"""Criptografia dos access tokens do WhatsApp (whatsapp_numbers.access_token_encrypted).

Mesma chave Fernet usada pelo worker para descriptografar antes de chamar o agents.
"""

from cryptography.fernet import Fernet

from app.core.config import settings


def _fernet() -> Fernet:
    if not settings.whatsapp_token_encryption_key:
        raise RuntimeError("WHATSAPP_TOKEN_ENCRYPTION_KEY não configurada")
    return Fernet(settings.whatsapp_token_encryption_key.encode())


def encrypt_access_token(access_token: str) -> str:
    return _fernet().encrypt(access_token.encode()).decode()


def decrypt_access_token(access_token_encrypted: str) -> str:
    return _fernet().decrypt(access_token_encrypted.encode()).decode()


def _tenant_fernet() -> Fernet:
    if not settings.tenant_stripe_key_encryption_key:
        raise RuntimeError("TENANT_STRIPE_KEY_ENCRYPTION_KEY não configurada")
    return Fernet(settings.tenant_stripe_key_encryption_key.encode())


def encrypt_tenant_secret(value: str) -> str:
    """Cifra a secret key OU o webhook secret da Stripe de um tenant — mesma
    chave Fernet serve pros dois valores, são independentes entre si."""
    return _tenant_fernet().encrypt(value.encode()).decode()


def decrypt_tenant_secret(value_encrypted: str) -> str:
    return _tenant_fernet().decrypt(value_encrypted.encode()).decode()
