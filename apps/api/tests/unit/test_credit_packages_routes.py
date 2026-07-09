import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.core.db import get_session
from app.main import app


def _package(name: str, active: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        price_brl=Decimal("100.00"),
        credits_granted=1000,
        active=active,
    )


def test_lista_so_pacotes_ativos() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [_package("Starter", True)]
    session.execute.return_value = result

    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).get("/api/v1/credit-packages")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Starter"
