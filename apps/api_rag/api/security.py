import os
import secrets

from dotenv import load_dotenv
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

load_dotenv()


API_KEY = os.getenv("API_KEY")  # na prática, vem de variável de ambiente
API_KEY_NAME = "Authorization"


if not API_KEY:
    raise RuntimeError("API_KEY não configurada")

api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)):
    if not api_key or not secrets.compare_digest(api_key, API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="API Key inválida ou ausente"
        )
    return api_key
