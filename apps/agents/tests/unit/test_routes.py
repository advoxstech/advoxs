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
        AsyncMock(
            return_value={"combined_message": None, "other_exec_is_running": True}
        ),
    )
    response = client.post("/messages", json=PAYLOAD)
    assert response.status_code == 202


def test_fluxo_feliz_envia_respostas_e_retorna_lista(client, monkeypatch):
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(
        return_value=(["resposta 1", "resposta 2"], 1234, "agente_secretaria")
    )
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    wa_cls, wa_instance = _mock_whatsapp_client(monkeypatch)

    response = client.post("/messages", json=PAYLOAD)

    assert response.status_code == 200
    assert response.json() == {
        "responses": ["resposta 1", "resposta 2"],
        "tokens_used": 1234,
        "current_agent": "agente_secretaria",
    }

    # thread_id composto por tenant + telefone do contato
    expected_thread = "tenant-1:5511999999999"
    assert debounce.call_args.kwargs["conversation_id"] == expected_thread
    assert run_agent.call_args.kwargs["conversation_id"] == expected_thread

    # cliente WhatsApp criado com as credenciais do tenant e chamado por resposta
    wa_cls.assert_called_once_with("111222333", "token-do-tenant")
    assert wa_instance.send_text_message.await_count == 2
    wa_instance.send_text_message.assert_awaited_with("5511999999999", "resposta 2")


def test_send_to_whatsapp_false_nao_envia_mas_retorna_respostas(client, monkeypatch):
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(
        return_value=(["resposta 1", "resposta 2"], 1234, "agente_condominial")
    )
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    wa_cls, wa_instance = _mock_whatsapp_client(monkeypatch)

    payload = {
        **PAYLOAD,
        "phone_number_id": "",
        "access_token": "",
        "send_to_whatsapp": False,
    }
    response = client.post("/messages", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "responses": ["resposta 1", "resposta 2"],
        "tokens_used": 1234,
        "current_agent": "agente_condominial",
    }
    wa_cls.assert_not_called()
    wa_instance.send_text_message.assert_not_awaited()


def test_send_to_whatsapp_default_true_continua_enviando(client, monkeypatch):
    debounce = AsyncMock(
        return_value={"combined_message": "olá", "other_exec_is_running": False}
    )
    run_agent = AsyncMock(return_value=(["resposta 1"], 100, None))
    monkeypatch.setattr(routes, "debounce_messages", debounce)
    monkeypatch.setattr(routes, "run_agent", run_agent)
    wa_cls, wa_instance = _mock_whatsapp_client(monkeypatch)

    response = client.post("/messages", json=PAYLOAD)

    assert response.status_code == 200
    assert response.json()["current_agent"] is None
    wa_cls.assert_called_once_with("111222333", "token-do-tenant")
    wa_instance.send_text_message.assert_awaited_once_with(
        "5511999999999", "resposta 1"
    )


def test_api_key_ausente_retorna_403(client, monkeypatch):
    monkeypatch.setattr(routes, "AGENTS_API_KEY", "segredo")
    response = client.post("/messages", json=PAYLOAD)
    assert response.status_code == 403


def test_api_key_correta_passa(client, monkeypatch):
    monkeypatch.setattr(routes, "AGENTS_API_KEY", "segredo")
    monkeypatch.setattr(
        routes,
        "debounce_messages",
        AsyncMock(
            return_value={"combined_message": None, "other_exec_is_running": True}
        ),
    )
    response = client.post(
        "/messages", json=PAYLOAD, headers={"Authorization": "segredo"}
    )
    assert response.status_code == 202


def test_resumo_sem_mensagens_retorna_400(client) -> None:
    response = client.post("/summaries", json={"messages": []})
    assert response.status_code == 400


def test_resumo_chama_summarize_conversation_e_retorna_resultado(client, monkeypatch) -> None:
    summarize = AsyncMock(return_value=("Resumo gerado.", 42))
    monkeypatch.setattr(routes, "summarize_conversation", summarize)

    response = client.post(
        "/summaries",
        json={
            "messages": [
                {"sender_type": "contact", "content": "Oi"},
                {"sender_type": "agent", "content": "Olá, como posso ajudar?"},
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == {"summary": "Resumo gerado.", "tokens_used": 42}
    summarize.assert_awaited_once_with(
        [
            {"sender_type": "contact", "content": "Oi"},
            {"sender_type": "agent", "content": "Olá, como posso ajudar?"},
        ]
    )


def test_resumo_erro_interno_retorna_500(client, monkeypatch) -> None:
    monkeypatch.setattr(
        routes, "summarize_conversation", AsyncMock(side_effect=RuntimeError("boom"))
    )

    response = client.post(
        "/summaries", json={"messages": [{"sender_type": "contact", "content": "oi"}]}
    )

    assert response.status_code == 500
