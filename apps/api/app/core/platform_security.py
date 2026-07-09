"""JWT do platform_admin — secret e claims separados dos tokens de tenant
(defesa em profundidade: um segredo vazado nunca forja o outro tipo de token)."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import settings

ALGORITHM = "HS256"
ACCESS_EXPIRES_MINUTES = 15
REFRESH_EXPIRES_DAYS = 30


def create_platform_access_token(admin_id: str, role: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(admin_id),
        "role": role,
        "type": "platform_access",
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_EXPIRES_MINUTES),
    }
    return jwt.encode(payload, settings.platform_jwt_secret, algorithm=ALGORITHM)


def create_platform_refresh_token(admin_id: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(admin_id),
        "jti": str(uuid.uuid4()),
        "type": "platform_refresh",
        "iat": now,
        "exp": now + timedelta(days=REFRESH_EXPIRES_DAYS),
    }
    return jwt.encode(payload, settings.platform_jwt_secret, algorithm=ALGORITHM)


def decode_platform_token(token: str) -> dict:
    """Decodifica e valida assinatura/expiração. Levanta jwt.PyJWTError se inválido."""
    return jwt.decode(token, settings.platform_jwt_secret, algorithms=[ALGORITHM])
