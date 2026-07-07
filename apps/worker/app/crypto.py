from cryptography.fernet import Fernet

from app.config import settings


def decrypt_access_token(access_token_encrypted: str) -> str:
    if not settings.whatsapp_token_encryption_key:
        raise RuntimeError("WHATSAPP_TOKEN_ENCRYPTION_KEY não configurada")
    fernet = Fernet(settings.whatsapp_token_encryption_key.encode())
    return fernet.decrypt(access_token_encrypted.encode()).decode()
