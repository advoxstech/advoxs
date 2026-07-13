import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app

TENANT_ID = uuid.uuid4()
PACKAGE_ID = uuid.uuid4()


def _package(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        id=PACKAGE_ID,
        tenant_id=TENANT_ID,
        name="Pacote Básico",
        price_brl=Decimal("49.90"),
        credits_granted=500,
        active=True,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _row_matches_where(stmt, row: SimpleNamespace) -> bool:
    """Avalia os binds compilados da query contra `row`, como o Postgres
    faria — em vez de simular "existe"/"não existe" incondicionalmente.

    Crucial para o caso de tenant isolation: se o predicado `tenant_id_1`
    estiver ausente dos binds (ex: um regression que removesse o filtro de
    `_get_package`), a função encontra a linha só pelo `id_1` — reproduzindo
    fielmente o bug de isolamento (a query real também encontraria a linha
    de outro tenant nesse cenário). Se o predicado estiver presente e não
    bater com o valor da linha, a linha não é encontrada — como o real
    `WHERE tenant_id = :tenant_id_1` faria.
    """
    params = dict(stmt.compile().params)
    if "id_1" in params and params["id_1"] != row.id:
        return False
    if "tenant_id_1" in params and params["tenant_id_1"] != row.tenant_id:
        return False
    return True


@pytest.fixture
def session():
    mock = AsyncMock()
    mock.add = MagicMock()
    return mock


@pytest.fixture
def client(session):
    async def override_ctx():
        return TenantContext(user_id=uuid.uuid4(), tenant_id=TENANT_ID, role="admin")

    async def override_session():
        yield session

    app.dependency_overrides[get_current_tenant] = override_ctx
    app.dependency_overrides[get_tenant_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_retorna_pacotes_do_tenant(client, session) -> None:
    result = MagicMock()
    result.scalars.return_value.all.return_value = [_package()]
    session.execute.return_value = result

    response = client.get("/api/v1/end-customer-billing/packages")

    assert response.status_code == 200
    assert response.json()[0]["name"] == "Pacote Básico"


def test_create_persiste_pacote(client, session) -> None:
    added = []
    session.add = MagicMock(side_effect=lambda obj: added.append(obj))

    async def fake_refresh(obj):
        obj.id = PACKAGE_ID

    session.refresh.side_effect = fake_refresh

    response = client.post(
        "/api/v1/end-customer-billing/packages",
        json={"name": "Growth", "price_brl": "99.90", "credits_granted": 1000},
    )

    assert response.status_code == 201
    assert len(added) == 1
    assert added[0].tenant_id == TENANT_ID
    assert added[0].name == "Growth"


def test_update_pacote_inexistente_retorna_404(client, session) -> None:
    session.scalar.return_value = None

    response = client.patch(
        f"/api/v1/end-customer-billing/packages/{PACKAGE_ID}",
        json={"active": False},
    )

    assert response.status_code == 404


def test_update_pacote_de_outro_tenant_retorna_404(client, session) -> None:
    """Simula a RLS/filtro explícito: o pacote existe (mesmo id, `PACKAGE_ID`),
    mas pertence a outro tenant — a query com tenant_id=TENANT_ID não deve
    encontrá-lo. Ao contrário de `session.scalar.return_value = None`, este
    double avalia de fato o predicado `tenant_id` dos binds compilados —
    detectaria uma regressão que removesse o filtro de `_get_package`."""
    package_de_outro_tenant = _package(tenant_id=uuid.uuid4())

    async def fake_scalar(stmt):
        if _row_matches_where(stmt, package_de_outro_tenant):
            return package_de_outro_tenant
        return None

    session.scalar = fake_scalar

    response = client.patch(
        f"/api/v1/end-customer-billing/packages/{PACKAGE_ID}", json={"active": False}
    )

    assert response.status_code == 404


def test_update_pacote_desativa(client, session) -> None:
    session.scalar.return_value = _package()

    response = client.patch(
        f"/api/v1/end-customer-billing/packages/{PACKAGE_ID}",
        json={"active": False},
    )

    assert response.status_code == 200
    assert response.json()["active"] is False


def test_delete_pacote_ja_usado_retorna_409(client, session) -> None:
    session.scalar = AsyncMock(side_effect=[_package(), uuid.uuid4()])

    response = client.delete(f"/api/v1/end-customer-billing/packages/{PACKAGE_ID}")

    assert response.status_code == 409


def test_delete_pacote_de_outro_tenant_retorna_404(client, session) -> None:
    """Mesmo caso do PATCH: o pacote existe mas é de outro tenant — o filtro
    de tenant_id em `_get_package` deve impedir o acesso antes de chegar na
    checagem de uso em `credit_transactions`. Usa o mesmo double de
    `_row_matches_where` para avaliar de fato o predicado `tenant_id`."""
    package_de_outro_tenant = _package(tenant_id=uuid.uuid4())

    async def fake_scalar(stmt):
        if _row_matches_where(stmt, package_de_outro_tenant):
            return package_de_outro_tenant
        return None

    session.scalar = fake_scalar

    response = client.delete(f"/api/v1/end-customer-billing/packages/{PACKAGE_ID}")

    assert response.status_code == 404


def test_delete_pacote_nao_usado_remove(client, session) -> None:
    package = _package()
    session.scalar = AsyncMock(side_effect=[package, None])
    session.delete = AsyncMock()

    response = client.delete(f"/api/v1/end-customer-billing/packages/{PACKAGE_ID}")

    assert response.status_code == 204
    session.delete.assert_awaited_once_with(package)
