import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.conversations as conversations_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.agents import AgentsApiError, AgentsNetworkError
from app.clients.whatsapp import WhatsAppSendError
from app.main import app

TENANT_ID = uuid.uuid4()
CONVERSATION_ID = uuid.uuid4()


def _conversation(
    state: str = "agent",
    summary: str | None = None,
    summary_generated_at=None,
    human_last_seen_at=None,
    is_test: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        contact_phone_number="5511999998888",
        state=state,
        last_message_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        summary=summary,
        summary_generated_at=summary_generated_at,
        human_last_seen_at=human_last_seen_at,
        is_test=is_test,
    )


def _number() -> SimpleNamespace:
    return SimpleNamespace(
        phone_number_id="PNID",
        access_token_encrypted="token-cifrado",
        status="connected",
    )


@pytest.fixture
def session():
    mock = AsyncMock()

    async def fake_refresh(obj):
        obj.id = uuid.uuid4()
        obj.created_at = datetime.now(UTC)

    mock.add = MagicMock()
    mock.refresh.side_effect = fake_refresh
    # Default seguro pra qualquer execute() não configurado explicitamente por
    # um teste — sem isso, o AsyncMock() bare propaga async recursivamente em
    # cadeias de atributo não configuradas (session.execute(...).all() viraria
    # uma coroutine, não uma lista, e o helper de saldo do cliente final
    # quebraria com "coroutine object is not iterable"). Testes que precisam
    # de um retorno específico continuam sobrescrevendo via
    # session.execute.return_value/.side_effect normalmente — essa linha só
    # cobre os que nunca tocam em session.execute hoje.
    mock.execute.return_value = _execute_returning([])
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


@pytest.fixture
def whatsapp_send(monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(conversations_module, "send_text_message", send)
    monkeypatch.setattr(
        conversations_module, "decrypt_access_token", MagicMock(return_value="token-claro")
    )
    return send


def _execute_returning(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _balance_result(rows: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/conversations")

    assert response.status_code == 401


class TestListConversations:
    def test_lista_conversas_do_tenant(self, client, session) -> None:
        session.execute.return_value = _execute_returning([_conversation()])

        response = client.get("/api/v1/conversations")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["id"] == str(CONVERSATION_ID)
        assert body[0]["state"] == "agent"


class TestOriginFilter:
    def test_default_exclui_conversas_de_teste(self, client, session) -> None:
        session.execute.return_value = _execute_returning([])

        response = client.get("/api/v1/conversations")

        assert response.status_code == 200
        # o filtro is_test = false entrou na query (direção importa: um
        # mapeamento origin→bool invertido passaria num assert só de presença)
        where_clause = str(
            session.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True})
        )
        assert "is_test = false" in where_clause

    def test_origin_test_filtra_conversas_de_teste(self, client, session) -> None:
        session.execute.return_value = _execute_returning([])

        response = client.get("/api/v1/conversations?origin=test")

        assert response.status_code == 200
        where_clause = str(
            session.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True})
        )
        assert "is_test = true" in where_clause

    def test_origin_invalido_retorna_422(self, client) -> None:
        response = client.get("/api/v1/conversations?origin=banana")

        assert response.status_code == 422


class TestListMessages:
    def test_lista_mensagens(self, client, session) -> None:
        session.scalar.return_value = _conversation()
        message = SimpleNamespace(
            id=uuid.uuid4(),
            sender_type="contact",
            content="Olá",
            media_url=None,
            media_type=None,
            delivery_status=None,
            created_at=datetime.now(UTC),
        )
        session.execute.return_value = _execute_returning([message])

        response = client.get(f"/api/v1/conversations/{CONVERSATION_ID}/messages")

        assert response.status_code == 200
        assert response.json()[0]["content"] == "Olá"

    def test_conversa_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.get(f"/api/v1/conversations/{uuid.uuid4()}/messages")

        assert response.status_code == 404


class TestTakeover:
    def test_altera_estado_para_human(self, client, session) -> None:
        conversation = _conversation(state="agent")
        session.scalar.return_value = conversation

        response = client.patch(f"/api/v1/conversations/{CONVERSATION_ID}", json={"state": "human"})

        assert response.status_code == 200
        assert conversation.state == "human"
        session.commit.assert_awaited_once()

    def test_estado_invalido_retorna_422(self, client) -> None:
        response = client.patch(f"/api/v1/conversations/{CONVERSATION_ID}", json={"state": "robo"})

        assert response.status_code == 422


class TestHeartbeat:
    def test_seta_human_last_seen_at_e_retorna_204(self, client, session) -> None:
        conversation = _conversation(state="human")
        session.scalar.return_value = conversation

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/heartbeat")

        assert response.status_code == 204
        assert conversation.human_last_seen_at is not None
        session.commit.assert_awaited()

    def test_conversa_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/heartbeat")

        assert response.status_code == 404


class TestPatchSetaPresenca:
    def test_virar_human_seta_human_last_seen_at(self, client, session) -> None:
        conversation = _conversation(state="agent")
        session.scalar.return_value = conversation

        response = client.patch(f"/api/v1/conversations/{CONVERSATION_ID}", json={"state": "human"})

        assert response.status_code == 200
        assert conversation.human_last_seen_at is not None

    def test_virar_agent_nao_seta_timestamp(self, client, session) -> None:
        conversation = _conversation(state="human")
        session.scalar.return_value = conversation

        response = client.patch(f"/api/v1/conversations/{CONVERSATION_ID}", json={"state": "agent"})

        assert response.status_code == 200
        assert conversation.human_last_seen_at is None


class TestSendMessage:
    def test_envia_e_persiste_como_human(self, client, session, whatsapp_send) -> None:
        session.scalar.side_effect = [_conversation(state="human"), _number()]

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages",
            json={"content": "Bom dia, aqui é o advogado"},
        )

        assert response.status_code == 201
        assert response.json()["sender_type"] == "human"
        whatsapp_send.assert_awaited_once_with(
            phone_number_id="PNID",
            access_token="token-claro",
            to="5511999998888",
            text="Bom dia, aqui é o advogado",
        )
        persisted = session.add.call_args.args[0]
        assert persisted.sender_type == "human"
        assert persisted.tenant_id == TENANT_ID
        assert persisted.delivery_status == "sent"
        assert response.json()["delivery_status"] == "sent"
        session.commit.assert_awaited_once()

    def test_conversa_em_modo_agente_retorna_409(self, client, session, whatsapp_send) -> None:
        session.scalar.side_effect = [_conversation(state="agent")]

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages", json={"content": "oi"}
        )

        assert response.status_code == 409
        whatsapp_send.assert_not_awaited()

    def test_sem_numero_conectado_retorna_409(self, client, session, whatsapp_send) -> None:
        session.scalar.side_effect = [_conversation(state="human"), None]

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages", json={"content": "oi"}
        )

        assert response.status_code == 409
        whatsapp_send.assert_not_awaited()

    def test_falha_na_graph_api_retorna_502(self, client, session, whatsapp_send) -> None:
        session.scalar.side_effect = [_conversation(state="human"), _number()]
        whatsapp_send.side_effect = WhatsAppSendError("HTTP 500")

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages", json={"content": "oi"}
        )

        assert response.status_code == 502
        session.add.assert_not_called()

    def test_conteudo_vazio_retorna_422(self, client) -> None:
        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages", json={"content": ""}
        )

        assert response.status_code == 422

    def test_resposta_humana_sincroniza_contexto_com_agents(
        self, client, session, whatsapp_send, monkeypatch
    ) -> None:
        session.scalar.side_effect = [_conversation(state="human"), _number()]
        sync_mock = AsyncMock()
        monkeypatch.setattr(conversations_module, "sync_conversation_context", sync_mock)

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages",
            json={"content": "olá, aqui é o Dr. Silva"},
        )

        assert response.status_code == 201
        sync_mock.assert_awaited_once()
        kwargs = sync_mock.await_args.kwargs
        assert kwargs["role"] == "attendant"
        assert kwargs["content"] == "olá, aqui é o Dr. Silva"
        assert kwargs["tenant_id"] == str(TENANT_ID)
        assert kwargs["contact_phone_number"] == "5511999998888"

    def test_falha_no_sync_nao_quebra_o_envio(
        self, client, session, whatsapp_send, monkeypatch
    ) -> None:
        session.scalar.side_effect = [_conversation(state="human"), _number()]
        sync_mock = AsyncMock(side_effect=AgentsNetworkError("agents fora do ar"))
        monkeypatch.setattr(conversations_module, "sync_conversation_context", sync_mock)

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages",
            json={"content": "olá"},
        )

        assert response.status_code == 201


class TestGenerateSummary:
    def test_saldo_esgotado_retorna_402(self, client, session, monkeypatch) -> None:
        session.scalar.return_value = _conversation(state="agent")
        session.get = AsyncMock(return_value=SimpleNamespace(credit_balance=0))
        summarize = AsyncMock()
        monkeypatch.setattr(conversations_module, "generate_conversation_summary", summarize)

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/summary")

        assert response.status_code == 402
        summarize.assert_not_awaited()

    def test_conversa_sem_mensagens_retorna_409(self, client, session) -> None:
        session.scalar.return_value = _conversation(state="agent")
        session.get = AsyncMock(return_value=SimpleNamespace(credit_balance=100))
        session.execute.return_value = _execute_returning([])

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/summary")

        assert response.status_code == 409

    def test_gera_resumo_persiste_e_debita_creditos(self, client, session, monkeypatch) -> None:
        conversation = _conversation(state="agent")
        session.scalar.return_value = conversation
        session.get = AsyncMock(return_value=SimpleNamespace(credit_balance=100))
        history = [
            SimpleNamespace(sender_type="contact", content="Oi, preciso de ajuda"),
            SimpleNamespace(sender_type="agent", content="Claro, qual é a dúvida?"),
        ]
        session.execute.return_value = _execute_returning(history)
        summarize = AsyncMock(
            return_value={
                "summary": "Resumo da conversa.",
                "tokens_used": 2500,
                "tokens_input": 2000,
                "tokens_output": 500,
            }
        )
        monkeypatch.setattr(conversations_module, "generate_conversation_summary", summarize)
        pricing = SimpleNamespace(
            id=uuid.uuid4(),
            tokens_per_credit=1000,
            input_weight=Decimal("0.3"),
            output_weight=Decimal("1.0"),
        )
        monkeypatch.setattr(
            conversations_module, "get_current_pricing_config", AsyncMock(return_value=pricing)
        )

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/summary")

        assert response.status_code == 200
        body = response.json()
        assert body["summary"] == "Resumo da conversa."
        assert conversation.summary == "Resumo da conversa."
        assert conversation.summary_generated_at is not None
        summarize.assert_awaited_once_with(
            [
                {"sender_type": "contact", "content": "Oi, preciso de ajuda"},
                {"sender_type": "agent", "content": "Claro, qual é a dúvida?"},
            ]
        )
        added = session.add.call_args.args[0]
        assert added.tenant_id == TENANT_ID
        assert added.type == "consumption"
        # 2000*0.3 + 500*1.0 = 1100 tokens ponderados -> 1.1 créditos -> arredonda pra 1
        assert added.amount_credits == Decimal("-1")
        assert added.related_message_id is None
        assert added.tokens_input == 2000
        assert added.tokens_output == 500
        assert added.pricing_config_id == pricing.id
        assert "token" not in added.description.lower()
        session.commit.assert_awaited_once()

    def test_erro_no_agents_retorna_502(self, client, session, monkeypatch) -> None:
        session.scalar.return_value = _conversation(state="agent")
        session.get = AsyncMock(return_value=SimpleNamespace(credit_balance=100))
        session.execute.return_value = _execute_returning(
            [SimpleNamespace(sender_type="contact", content="oi")]
        )
        monkeypatch.setattr(
            conversations_module,
            "generate_conversation_summary",
            AsyncMock(side_effect=AgentsApiError("agents HTTP 500")),
        )

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/summary")

        assert response.status_code == 502

    def test_falha_de_rede_no_agents_retorna_502(self, client, session, monkeypatch) -> None:
        session.scalar.return_value = _conversation(state="agent")
        session.get = AsyncMock(return_value=SimpleNamespace(credit_balance=100))
        session.execute.return_value = _execute_returning(
            [SimpleNamespace(sender_type="contact", content="oi")]
        )
        monkeypatch.setattr(
            conversations_module,
            "generate_conversation_summary",
            AsyncMock(side_effect=AgentsNetworkError("timeout")),
        )

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/summary")

        assert response.status_code == 502

    def test_conversa_de_outro_tenant_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/summary")

        assert response.status_code == 404


class TestDeleteConversation:
    def test_apaga_conversa_real_com_sucesso(self, client, session, monkeypatch) -> None:
        checkpoint_mock = AsyncMock()
        monkeypatch.setattr(conversations_module, "delete_agent_checkpoint", checkpoint_mock)
        session.scalar.return_value = _conversation()

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 204
        session.delete.assert_awaited_once()
        session.commit.assert_awaited()
        checkpoint_mock.assert_awaited_once_with(f"{TENANT_ID}:5511999998888")

    def test_apaga_conversa_de_teste_tambem(self, client, session, monkeypatch) -> None:
        # A rota generalizada não distingue origem — conversa de teste também
        # pode ser apagada por aqui (o botão de teste continua existindo no
        # front, mas o backend não faz mais essa distinção).
        monkeypatch.setattr(conversations_module, "delete_agent_checkpoint", AsyncMock())
        session.scalar.return_value = _conversation(is_test=True)

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 204

    def test_desvincula_ledger_do_tenant_e_do_cliente_final_antes_de_apagar(
        self, client, session, monkeypatch
    ) -> None:
        monkeypatch.setattr(conversations_module, "delete_agent_checkpoint", AsyncMock())
        session.scalar.return_value = _conversation()

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 204
        # três executes: UPDATE credit_transactions, UPDATE
        # end_customer_credit_transactions e DELETE messages, nessa ordem.
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        tenant_ledger_idx = next(
            i
            for i, s in enumerate(statements)
            if "credit_transactions" in s and "end_customer" not in s
        )
        end_customer_ledger_idx = next(
            i for i, s in enumerate(statements) if "end_customer_credit_transactions" in s
        )
        delete_idx = next(i for i, s in enumerate(statements) if "DELETE FROM messages" in s)
        assert tenant_ledger_idx < delete_idx
        assert end_customer_ledger_idx < delete_idx

    def test_conversa_inexistente_retorna_404(self, client, session, monkeypatch) -> None:
        checkpoint_mock = AsyncMock()
        monkeypatch.setattr(conversations_module, "delete_agent_checkpoint", checkpoint_mock)
        session.scalar.return_value = None

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 404
        session.delete.assert_not_awaited()
        checkpoint_mock.assert_not_awaited()

    def test_falha_no_checkpoint_nao_impede_a_exclusao(self, client, session, monkeypatch) -> None:
        # delete_agent_checkpoint já engole a própria exceção (best-effort) —
        # aqui só confirmamos que a rota não depende do retorno dele.
        checkpoint_mock = AsyncMock(return_value=None)
        monkeypatch.setattr(conversations_module, "delete_agent_checkpoint", checkpoint_mock)
        session.scalar.return_value = _conversation()

        response = client.delete(f"/api/v1/conversations/{CONVERSATION_ID}")

        assert response.status_code == 204
        checkpoint_mock.assert_awaited_once()


class TestEndCustomerBalance:
    def test_lista_inclui_saldo_do_cliente_final_quando_ha_registro(
        self, client, session
    ) -> None:
        session.execute.side_effect = [
            _execute_returning([_conversation()]),
            _balance_result(
                [
                    SimpleNamespace(
                        contact_phone_number="5511999998888", credit_balance=Decimal("42")
                    )
                ]
            ),
        ]

        response = client.get("/api/v1/conversations")

        assert response.status_code == 200
        assert response.json()[0]["end_customer_balance"] == 42.0

    def test_lista_sem_saldo_encontrado_retorna_null(self, client, session) -> None:
        session.execute.side_effect = [
            _execute_returning([_conversation()]),
            _balance_result([]),
        ]

        response = client.get("/api/v1/conversations")

        assert response.status_code == 200
        assert response.json()[0]["end_customer_balance"] is None

    def test_lista_vazia_nao_consulta_saldo(self, client, session) -> None:
        session.execute.return_value = _execute_returning([])

        response = client.get("/api/v1/conversations")

        assert response.status_code == 200
        assert response.json() == []
        # Sem conversas, não há telefone pra buscar saldo — só 1 chamada a execute.
        session.execute.assert_awaited_once()

    def test_takeover_devolve_saldo_do_cliente_final(self, client, session) -> None:
        conversation = _conversation(state="agent")
        session.scalar.return_value = conversation
        session.execute.side_effect = [
            _balance_result(
                [
                    SimpleNamespace(
                        contact_phone_number="5511999998888", credit_balance=Decimal("7")
                    )
                ]
            ),
        ]

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}", json={"state": "human"}
        )

        assert response.status_code == 200
        assert response.json()["end_customer_balance"] == 7.0

    def test_resumo_devolve_saldo_do_cliente_final(self, client, session, monkeypatch) -> None:
        conversation = _conversation(state="agent")
        session.scalar.return_value = conversation
        session.get = AsyncMock(return_value=SimpleNamespace(credit_balance=100))
        session.execute.side_effect = [
            _execute_returning([SimpleNamespace(sender_type="contact", content="oi")]),
            _balance_result(
                [
                    SimpleNamespace(
                        contact_phone_number="5511999998888", credit_balance=Decimal("15")
                    )
                ]
            ),
        ]
        monkeypatch.setattr(
            conversations_module,
            "generate_conversation_summary",
            AsyncMock(return_value={"summary": "Resumo.", "tokens_used": 100}),
        )
        pricing = SimpleNamespace(
            id=uuid.uuid4(),
            tokens_per_credit=1000,
            input_weight=Decimal("0.3"),
            output_weight=Decimal("1.0"),
        )
        monkeypatch.setattr(
            conversations_module, "get_current_pricing_config", AsyncMock(return_value=pricing)
        )

        response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/summary")

        assert response.status_code == 200
        assert response.json()["end_customer_balance"] == 15.0

    def test_saldo_filtra_por_billing_habilitado(self, client, session) -> None:
        session.execute.side_effect = [
            _execute_returning([_conversation()]),
            _balance_result([]),
        ]

        client.get("/api/v1/conversations")

        balance_query = session.execute.await_args_list[1].args[0]
        compiled = str(balance_query.compile(compile_kwargs={"literal_binds": True}))
        assert "tenant_billing_settings" in compiled
        assert "enabled IS true" in compiled
