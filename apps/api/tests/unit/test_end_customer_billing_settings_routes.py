import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app

TENANT_ID = uuid.uuid4()


def _settings_row(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        enabled=False,
        billing_mode="credits",
        stripe_secret_key_encrypted=None,
        stripe_webhook_secret_encrypted=None,
        end_customer_tokens_per_credit=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


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


def test_get_sem_configuracao_retorna_default(client, session) -> None:
    session.scalar.return_value = None

    response = client.get("/api/v1/end-customer-billing/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["stripe_secret_key_configured"] is False
    assert body["stripe_webhook_secret_configured"] is False


def test_get_retorna_tenant_id_para_montar_url_do_webhook(client, session) -> None:
    session.scalar.return_value = None

    response = client.get("/api/v1/end-customer-billing/settings")

    assert response.json()["tenant_id"] == str(TENANT_ID)


def test_get_retorna_webhook_url_completa(client, session, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "api_public_url", "https://api.exemplo.com.br")
    session.scalar.return_value = None

    response = client.get("/api/v1/end-customer-billing/settings")

    assert response.json()["webhook_url"] == (
        f"https://api.exemplo.com.br/api/v1/webhooks/stripe/tenant/{TENANT_ID}"
    )


def test_get_sem_api_public_url_degrada_pra_path_relativo(client, session, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "api_public_url", "")
    session.scalar.return_value = None

    response = client.get("/api/v1/end-customer-billing/settings")

    assert response.json()["webhook_url"] == f"/api/v1/webhooks/stripe/tenant/{TENANT_ID}"


def test_get_com_configuracao_nao_revela_secrets(client, session) -> None:
    session.scalar.return_value = _settings_row(
        stripe_secret_key_encrypted="cifrado", stripe_webhook_secret_encrypted="cifrado-2"
    )

    response = client.get("/api/v1/end-customer-billing/settings")

    body = response.json()
    assert body["stripe_secret_key_configured"] is True
    assert "stripe_secret_key_encrypted" not in body
    assert "stripe_secret_key" not in body


def test_patch_sem_secret_key_e_sem_habilitar_nao_exige_nada(client, session) -> None:
    session.scalar.return_value = None

    response = client.patch(
        "/api/v1/end-customer-billing/settings", json={"end_customer_tokens_per_credit": 500}
    )

    assert response.status_code == 200
    session.commit.assert_awaited_once()


def test_patch_habilitar_sem_secret_key_configurada_retorna_400(client, session) -> None:
    session.scalar.return_value = None

    response = client.patch("/api/v1/end-customer-billing/settings", json={"enabled": True})

    assert response.status_code == 400


def test_patch_habilitar_sem_tokens_per_credit_funciona(client, session) -> None:
    """A proporção token/crédito é global (pricing_configs, Etapa 2) — não é
    mais responsabilidade do tenant configurar, então habilitar não exige
    end_customer_tokens_per_credit (coluna deprecada)."""
    session.scalar.return_value = _settings_row(stripe_secret_key_encrypted="cifrado")

    response = client.patch("/api/v1/end-customer-billing/settings", json={"enabled": True})

    assert response.status_code == 200
    assert response.json()["enabled"] is True


def test_patch_habilitar_com_tudo_configurado_funciona(client, session) -> None:
    session.scalar.return_value = _settings_row(
        stripe_secret_key_encrypted="cifrado", end_customer_tokens_per_credit=500
    )

    response = client.patch("/api/v1/end-customer-billing/settings", json={"enabled": True})

    assert response.status_code == 200
    assert response.json()["enabled"] is True


def test_patch_habilitar_sem_pacote_ativo_retorna_400(client, session) -> None:
    session.scalar.side_effect = [
        _settings_row(stripe_secret_key_encrypted="cifrado"),  # _get_settings_row
        None,  # checagem de pacote ativo — nenhum cadastrado
    ]

    response = client.patch("/api/v1/end-customer-billing/settings", json={"enabled": True})

    assert response.status_code == 400
    assert "pacote" in response.json()["detail"].lower()


def test_patch_habilitar_com_pacote_ativo_funciona(client, session) -> None:
    session.scalar.side_effect = [
        _settings_row(stripe_secret_key_encrypted="cifrado"),  # _get_settings_row
        uuid.uuid4(),  # checagem de pacote ativo — existe pelo menos 1
    ]

    response = client.patch("/api/v1/end-customer-billing/settings", json={"enabled": True})

    assert response.status_code == 200
    assert response.json()["enabled"] is True


def test_patch_habilitar_sozinho_preserva_secrets_ja_configurados_na_resposta(
    client, session
) -> None:
    session.scalar.return_value = _settings_row(
        stripe_secret_key_encrypted="cifrado",
        stripe_webhook_secret_encrypted="cifrado-webhook",
        end_customer_tokens_per_credit=500,
    )

    response = client.patch("/api/v1/end-customer-billing/settings", json={"enabled": True})

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["stripe_secret_key_configured"] is True
    assert body["stripe_webhook_secret_configured"] is True


def test_patch_secret_key_tokens_e_enabled_juntos_funciona(client, session, monkeypatch) -> None:
    session.scalar.side_effect = [
        None,  # _get_settings_row — ainda não existe registro
        uuid.uuid4(),  # checagem de pacote ativo — existe pelo menos 1
    ]
    monkeypatch.setattr(
        "app.api.v1.end_customer_billing.encrypt_tenant_secret", lambda v: f"cifrado:{v}"
    )

    response = client.patch(
        "/api/v1/end-customer-billing/settings",
        json={
            "stripe_secret_key": "sk_test_123",
            "end_customer_tokens_per_credit": 300,
            "enabled": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["stripe_secret_key_configured"] is True
    assert body["end_customer_tokens_per_credit"] == 300


def test_patch_cria_registro_quando_nao_existe(client, session, monkeypatch) -> None:
    session.scalar.return_value = None
    added = []
    session.add = MagicMock(side_effect=lambda obj: added.append(obj))
    monkeypatch.setattr(
        "app.api.v1.end_customer_billing.encrypt_tenant_secret", lambda v: f"cifrado:{v}"
    )

    response = client.patch(
        "/api/v1/end-customer-billing/settings",
        json={"stripe_secret_key": "sk_test_123", "end_customer_tokens_per_credit": 300},
    )

    assert response.status_code == 200
    assert len(added) == 1
    created = added[0]
    assert created.tenant_id == TENANT_ID
    assert created.stripe_secret_key_encrypted == "cifrado:sk_test_123"
    assert created.end_customer_tokens_per_credit == 300


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/end-customer-billing/settings")
    assert response.status_code == 401
