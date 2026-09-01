import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.test_conversations as test_conversations_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.agents import AgentsNetworkError
from app.main import app

TENANT_ID = uuid.uuid4()
CONVERSATION_ID = uuid.uuid4()


def _conversation(is_test: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        contact_phone_number="teste-abc123def456",
        state="agent",
        is_test=is_test,
        last_message_at=None,
        created_at=__import__("datetime").datetime(2026, 7, 16, tzinfo=__import__("datetime").UTC),
        summary=None,
        summary_generated_at=None,
    )


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


class TestCreate:
    def test_cria_conversa_de_teste(self, client, session) -> None:
        async def fake_refresh(obj):
            obj.id = CONVERSATION_ID
            obj.state = "agent"
            obj.created_at = _conversation().created_at
            obj.last_message_at = None
            obj.summary = None
            obj.summary_generated_at = None
            obj.end_customer_billing_exempt = False

        session.refresh.side_effect = fake_refresh

        response = client.post("/api/v1/test-conversations")

        assert response.status_code == 201
        body = response.json()
        assert body["is_test"] is True
        assert body["contact_phone_number"].startswith("teste-")
        session.add.assert_called_once()
        session.commit.assert_awaited()


class TestSendTestMessage:
    @pytest.fixture
    def playground_mock(self, monkeypatch):
        mock = AsyncMock(
            return_value={
                "responses": ["resposta 1", "resposta 2"],
                "tokens_used": 3500,
                "tokens_input": 2800,
                "tokens_output": 700,
                "current_agent": "agente_secretaria",
            }
        )
        monkeypatch.setattr(test_conversations_module.service, "send_playground_message", mock)
        monkeypatch.setattr(
            test_conversations_module.service,
            "load_agents_for_engine",
            AsyncMock(return_value=[]),
        )
        pricing = SimpleNamespace(
            id=uuid.uuid4(),
            tokens_per_credit=1000,
            input_weight=Decimal("0.3"),
            output_weight=Decimal("1.0"),
        )
        monkeypatch.setattr(
            test_conversations_module.service,
            "get_current_pricing_config",
            AsyncMock(return_value=pricing),
        )
        return mock

    def _arm_session(self, session, conversation, balance=1000):
        # scalar: 1ª chamada resolve a conversa; get: tenant com saldo
        session.scalar.return_value = conversation
        session.get.return_value = SimpleNamespace(id=TENANT_ID, credit_balance=balance)

        async def fake_refresh(obj):
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if getattr(obj, "created_at", None) is None:
                obj.created_at = conversation.created_at
            for campo in ("media_url", "media_type", "delivery_status"):
                if not hasattr(obj, campo):
                    setattr(obj, campo, None)

        session.refresh.side_effect = fake_refresh

    def test_fluxo_feliz_persiste_e_debita(self, client, session, playground_mock) -> None:
        self._arm_session(session, _conversation())

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            data={"content": "olá, quero saber sobre condomínio"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["grouped"] is False
        assert len(body["messages"]) == 3  # contato + 2 respostas
        assert body["messages"][0]["sender_type"] == "contact"
        assert body["messages"][1]["sender_type"] == "agent"
        playground_mock.assert_awaited_once()
        assert playground_mock.await_args.kwargs["contact_phone_number"] == "teste-abc123def456"
        # Último add é o lançamento do ledger — com os tokens brutos auditados.
        transaction = session.add.call_args.args[0]
        assert transaction.type == "consumption"
        # 2800*0.3 + 700*1.0 = 1540 tokens ponderados -> 1.54 créditos -> arredonda pra 2
        assert transaction.amount_credits == Decimal("-2")
        assert transaction.tokens_input == 2800
        assert transaction.tokens_output == 700
        assert "token" not in transaction.description.lower()

    def test_conversa_real_retorna_409(self, client, session, playground_mock) -> None:
        self._arm_session(session, _conversation(is_test=False))

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            data={"content": "oi"},
        )

        assert response.status_code == 409
        playground_mock.assert_not_awaited()

    def test_sem_saldo_retorna_402(self, client, session, playground_mock) -> None:
        self._arm_session(session, _conversation(), balance=0)

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            data={"content": "oi"},
        )

        assert response.status_code == 402
        playground_mock.assert_not_awaited()

    def test_grouped_nao_persiste_resposta(self, client, session, playground_mock) -> None:
        playground_mock.return_value = None
        self._arm_session(session, _conversation())

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            data={"content": "oi"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["grouped"] is True
        assert len(body["messages"]) == 1  # só a do contato

    def test_falha_do_agents_retorna_502(self, client, session, playground_mock) -> None:
        playground_mock.side_effect = AgentsNetworkError("fora do ar")
        self._arm_session(session, _conversation())

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            data={"content": "oi"},
        )

        assert response.status_code == 502
        # a mensagem do contato foi commitada antes da chamada
        session.commit.assert_awaited()

    def test_agents_do_tenant_e_repassado_ao_send_playground_message(
        self, client, session, playground_mock, monkeypatch
    ) -> None:
        self._arm_session(session, _conversation())
        agents_payload = [
            {
                "id": "a1",
                "name": "Secretária",
                "instructions": "x",
                "is_entry_point": True,
                "knowledge_base_file_ids": [],
            }
        ]
        monkeypatch.setattr(
            test_conversations_module.service,
            "load_agents_for_engine",
            AsyncMock(return_value=agents_payload),
        )

        client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            data={"content": "oi"},
        )

        assert playground_mock.await_args.kwargs["agents"] == agents_payload

    def test_persiste_current_agent_id_na_conversa(self, client, session, playground_mock) -> None:
        agent_id = uuid.uuid4()
        playground_mock.return_value = {
            "responses": ["oi"],
            "tokens_used": 100,
            "current_agent_id": str(agent_id),
        }
        conversation = _conversation()
        self._arm_session(session, conversation)

        client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            data={"content": "oi"},
        )

        assert conversation.current_agent_id == agent_id

    def test_documento_gerado_soma_custo_fixo_e_persiste_media_url(
        self, client, session, playground_mock
    ) -> None:
        playground_mock.return_value = {
            "responses": ["segue a multa"],
            "tokens_used": 100,
            "tokens_input": 70,
            "tokens_output": 30,
            "current_agent": "Condominial",
            "documents": [
                {
                    "link": "https://agents.exemplo.com/generated-documents/doc-1",
                    "filename": "Multa.pdf",
                    "credit_cost": 20,
                    "delivered": False,
                }
            ],
        }
        self._arm_session(session, _conversation())

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            data={"content": "quero uma multa"},
        )

        assert response.status_code == 201
        added_agent_messages = [
            call.args[0]
            for call in session.add.call_args_list
            if getattr(call.args[0], "sender_type", None) == "agent"
        ]
        doc_message = next(m for m in added_agent_messages if m.media_url is not None)
        assert doc_message.media_url == "https://agents.exemplo.com/generated-documents/doc-1"
        assert doc_message.media_type == "application/pdf"
        assert "Multa.pdf" in doc_message.content

        # 70*0.3 + 30*1.0 = 51 tokens ponderados -> 0.051 créditos -> arredonda
        # pra 0, mais os 20 créditos fixos do documento gerado.
        transaction = session.add.call_args.args[0]
        assert transaction.amount_credits == Decimal("-20")

    def test_sem_current_agent_id_nao_seta_atributo(self, client, session, playground_mock) -> None:
        conversation = _conversation()
        self._arm_session(session, conversation)

        client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            data={"content": "oi"},
        )

        assert getattr(conversation, "current_agent_id", None) is None

    def test_conversa_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            data={"content": "oi"},
        )

        assert response.status_code == 404

    def test_sem_conteudo_e_sem_anexo_retorna_400(self, client, session) -> None:
        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            data={"content": "   "},
        )

        assert response.status_code == 400

    def test_anexo_processado_tem_nota_anexada_a_mensagem_do_agente(
        self, client, session, playground_mock, monkeypatch
    ) -> None:
        attachment_mock = AsyncMock(
            return_value="[Documento recebido e processado: laudo.pdf — disponível para busca.]"
        )
        monkeypatch.setattr(
            test_conversations_module.service, "process_test_attachment", attachment_mock
        )
        self._arm_session(session, _conversation())

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            data={"content": "segue o laudo"},
            files={"file": ("laudo.pdf", b"%PDF-1.4", "application/pdf")},
        )

        assert response.status_code == 201
        attachment_mock.assert_awaited_once()
        kwargs = attachment_mock.await_args.kwargs
        assert kwargs["tenant_id"] == str(TENANT_ID)
        assert kwargs["conversation_id"] == "teste-abc123def456"
        assert playground_mock.await_args.kwargs["message"] == (
            "segue o laudo\n[Documento recebido e processado: laudo.pdf — disponível para busca.]"
        )

    def test_anexo_sem_legenda_usa_nome_do_arquivo_no_historico(
        self, client, session, playground_mock, monkeypatch
    ) -> None:
        attachment_mock = AsyncMock(
            return_value="[Documento recebido e processado: laudo.pdf — disponível para busca.]"
        )
        monkeypatch.setattr(
            test_conversations_module.service, "process_test_attachment", attachment_mock
        )
        self._arm_session(session, _conversation())

        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            data={"content": ""},
            files={"file": ("laudo.pdf", b"%PDF-1.4", "application/pdf")},
        )

        assert response.status_code == 201
        contact_message = session.add.call_args_list[0].args[0]
        assert contact_message.content == "📎 laudo.pdf"
        assert playground_mock.await_args.kwargs["message"] == (
            "[Documento recebido e processado: laudo.pdf — disponível para busca.]"
        )

    def test_sem_anexo_nao_chama_process_test_attachment(
        self, client, session, playground_mock, monkeypatch
    ) -> None:
        attachment_mock = AsyncMock()
        monkeypatch.setattr(
            test_conversations_module.service, "process_test_attachment", attachment_mock
        )
        self._arm_session(session, _conversation())

        client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/test-messages",
            data={"content": "oi"},
        )

        attachment_mock.assert_not_awaited()
        assert playground_mock.await_args.kwargs["message"] == "oi"
