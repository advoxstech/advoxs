from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import api.routes as routes


PAYLOAD = {
    "tenant_id": "tenant-1",
    "contact_phone_number": "5511999999999",
    "message": "olá",
    "attachments": [],
    "phone_number_id": "111222333",
    "access_token": "token-do-tenant",
}


@pytest.fixture
def client():
    return TestClient(routes.app)


def _mock_whatsapp_client(monkeypatch):
    instance = MagicMock()
    instance.send_text_message = AsyncMock(return_value={"success": True})
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    cls = MagicMock(return_value=instance)
    monkeypatch.setattr(routes, "WhatsAppClient", cls)
    return cls, instance


def test_mensagem_vazia_sem_anexos_retorna_400(client):
    payload = {**PAYLOAD, "message": "", "attachments": []}
    response = client.post("/messages", json=payload)
    assert response.status_code == 400


def test_payload_sem_tenant_id_retorna_422(client):
    payload = {k: v for k, v in PAYLOAD.items() if k != "tenant_id"}
    response = client.post("/messages", json=payload)
    assert response.status_code == 422


def test_execucao_concorrente_retorna_202(client, monkeypatch):
    monkeypatch.setattr(
        routes,
        "debounce_messages",
        AsyncMock(return_value={"combined_message": None, "other_exec_is_running": True}),
    )
    response = client.post("/messages", json=PAYLOAD)
    assert response.status_code == 202


def test_fluxo_feliz_envia_respostas_e_retorna_lista(client, monkeypatch):
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(return_value=(["resposta 1", "resposta 2"], 1234))
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    wa_cls, wa_instance = _mock_whatsapp_client(monkeypatch)

    response = client.post("/messages", json=PAYLOAD)

    assert response.status_code == 200
    assert response.json() == {
        "responses": ["resposta 1", "resposta 2"],
        "tokens_used": 1234,
    }

    # thread_id composto por tenant + telefone do contato
    expected_thread = "tenant-1:5511999999999"
    assert debounce.call_args.kwargs["conversation_id"] == expected_thread
    assert run_agent.call_args.kwargs["conversation_id"] == expected_thread

    # cliente WhatsApp criado com as credenciais do tenant e chamado por resposta
    wa_cls.assert_called_once_with("111222333", "token-do-tenant")
    assert wa_instance.send_text_message.await_count == 2
    wa_instance.send_text_message.assert_awaited_with("5511999999999", "resposta 2")


def test_api_key_ausente_retorna_403(client, monkeypatch):
    monkeypatch.setattr(routes, "AGENTS_API_KEY", "segredo")
    response = client.post("/messages", json=PAYLOAD)
    assert response.status_code == 403


def test_api_key_correta_passa(client, monkeypatch):
    monkeypatch.setattr(routes, "AGENTS_API_KEY", "segredo")
    monkeypatch.setattr(
        routes,
        "debounce_messages",
        AsyncMock(return_value={"combined_message": None, "other_exec_is_running": True}),
    )
    response = client.post(
        "/messages", json=PAYLOAD, headers={"Authorization": "segredo"}
    )
    assert response.status_code == 202
