import pytest
from fastapi import HTTPException

from app.api.internal_deps import verify_internal_service_key
from app.core.config import settings


async def test_sem_env_configurada_nao_bloqueia(monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_service_key", "")

    await verify_internal_service_key(authorization=None)


async def test_sem_header_com_env_configurada_levanta_403(monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_service_key", "chave-secreta")

    with pytest.raises(HTTPException) as exc_info:
        await verify_internal_service_key(authorization=None)
    assert exc_info.value.status_code == 403


async def test_header_incorreto_levanta_403(monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_service_key", "chave-secreta")

    with pytest.raises(HTTPException) as exc_info:
        await verify_internal_service_key(authorization="chave-errada")
    assert exc_info.value.status_code == 403


async def test_header_correto_nao_levanta(monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_service_key", "chave-secreta")

    await verify_internal_service_key(authorization="chave-secreta")
