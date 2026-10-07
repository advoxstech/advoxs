import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app
from app.services.urgency import (
    DEFAULT_URGENCY_KEYWORDS,
    MAX_KEYWORDS_PER_AGENT,
    build_default_urgency_keywords,
    clear_urgent,
    flag_contact_message,
    mark_urgent,
    match_keyword,
    normalize,
)

TENANT_ID = uuid.uuid4()
CONVERSATION_ID = uuid.uuid4()
AGENT_ID = uuid.uuid4()


def _keyword(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=AGENT_ID,
        keyword=text,
        normalized=normalize(text),
        created_at=datetime.now(UTC),
    )


def _conversation(**overrides) -> SimpleNamespace:
    values = {
        "id": CONVERSATION_ID,
        "tenant_id": TENANT_ID,
        "contact_phone_number": "5511999998888",
        "state": "agent",
        "automation_status": "idle",
        "is_test": False,
        "last_message_at": datetime.now(UTC),
        "created_at": datetime.now(UTC),
        "summary": None,
        "summary_generated_at": None,
        "end_customer_billing_exempt": False,
        "current_agent_id": None,
        "urgent_since": None,
        "urgent_reason": None,
        "urgent_source": None,
        "urgent_agent_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestNormalizeAndMatch:
    def test_normaliza_acento_caixa_e_espacos(self) -> None:
        assert normalize("  Audiência   AMANHÃ ") == "audiencia amanha"

    def test_casa_palavra_inteira_ignorando_acento(self) -> None:
        keywords = [_keyword("prisão"), _keyword("audiência amanhã")]

        assert match_keyword("Meu filho teve PRISAO decretada", keywords).keyword == "prisão"
        assert match_keyword("tenho audiencia amanha cedo", keywords).keyword == (
            "audiência amanhã"
        )

    def test_nao_casa_dentro_de_outra_palavra(self) -> None:
        assert match_keyword("o rio está represo", [_keyword("preso")]) is None

    def test_texto_vazio_ou_sem_palavras(self) -> None:
        assert match_keyword("", [_keyword("preso")]) is None
        assert match_keyword("bom dia", []) is None

    def test_lista_padrao_sem_duplicadas_normalizadas(self) -> None:
        normalized = [k.normalized for k in build_default_urgency_keywords(TENANT_ID, AGENT_ID)]
        assert len(normalized) == len(set(normalized)) == len(DEFAULT_URGENCY_KEYWORDS)


class TestMarkUrgent:
    def test_primeira_sinalizacao_preenche_tudo(self) -> None:
        conversation = _conversation()

        mark_urgent(
            conversation,
            'Palavra-chave: "preso"',
            "keyword",
            agent_id=AGENT_ID,
        )

        assert conversation.urgent_since is not None
        assert conversation.urgent_source == "keyword"
        assert conversation.urgent_agent_id == AGENT_ID

    def test_mantem_horario_e_motivo_do_agente_prevalece(self) -> None:
        since = datetime(2026, 1, 1, tzinfo=UTC)
        conversation = _conversation(
            urgent_since=since, urgent_reason="Palavra-chave", urgent_source="keyword"
        )

        mark_urgent(conversation, "audiência amanhã", "agent")

        assert conversation.urgent_since == since
        assert conversation.urgent_reason == "audiência amanhã"
        assert conversation.urgent_source == "agent"
        assert conversation.urgent_agent_id is None

    def test_palavra_chave_nao_sobrescreve_motivo_do_agente(self) -> None:
        conversation = _conversation(
            urgent_since=datetime.now(UTC), urgent_reason="audiência", urgent_source="agent"
        )

        mark_urgent(conversation, "Palavra-chave", "keyword")

        assert conversation.urgent_reason == "audiência"

    def test_motivo_limitado(self) -> None:
        conversation = _conversation()
        mark_urgent(conversation, "x" * 500, "agent")
        assert len(conversation.urgent_reason) == 300

    def test_clear_limpa_as_quatro_colunas(self) -> None:
        conversation = _conversation(
            urgent_since=datetime.now(UTC),
            urgent_reason="x",
            urgent_source="keyword",
            urgent_agent_id=AGENT_ID,
        )
        clear_urgent(conversation)
        assert (
            conversation.urgent_since,
            conversation.urgent_reason,
            conversation.urgent_source,
            conversation.urgent_agent_id,
        ) == (None, None, None, None)


class TestFlagContactMessage:
    async def test_marca_quando_mensagem_tem_palavra_chave(self) -> None:
        session = AsyncMock()
        session.scalars.return_value = [_keyword("despejo")]
        conversation = _conversation(current_agent_id=AGENT_ID)

        await flag_contact_message(session, conversation, "Recebi ordem de DESPEJO hoje")

        assert conversation.urgent_reason == 'Palavra-chave: "despejo"'
        assert conversation.urgent_source == "keyword"
        assert conversation.urgent_agent_id == AGENT_ID

    async def test_sem_conteudo_nem_consulta(self) -> None:
        session = AsyncMock()
        conversation = _conversation()

        await flag_contact_message(session, conversation, "")

        session.scalars.assert_not_awaited()
        assert conversation.urgent_since is None


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


class TestKeywordRoutes:
    def test_lista(self, client, session) -> None:
        session.scalar.return_value = AGENT_ID
        session.scalars.return_value = [_keyword("despejo")]

        response = client.get(f"/api/v1/agents/{AGENT_ID}/urgency-keywords")

        assert response.status_code == 200
        assert [k["keyword"] for k in response.json()] == ["despejo"]
        statement = str(session.scalars.await_args.args[0])
        assert "urgency_keywords.agent_id" in statement

    def test_agente_de_outro_escritorio_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.get(f"/api/v1/agents/{AGENT_ID}/urgency-keywords")

        assert response.status_code == 404
        session.scalars.assert_not_awaited()

    def test_adiciona_normalizando_espacos(self, client, session) -> None:
        session.scalar.side_effect = [AGENT_ID, 3]

        async def fake_refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime.now(UTC)

        session.refresh.side_effect = fake_refresh

        response = client.post(
            f"/api/v1/agents/{AGENT_ID}/urgency-keywords",
            json={"keyword": "  Audiência   hoje "},
        )

        assert response.status_code == 201
        assert response.json()["keyword"] == "Audiência hoje"
        added = session.add.call_args.args[0]
        assert added.normalized == "audiencia hoje"
        assert added.tenant_id == TENANT_ID
        assert added.agent_id == AGENT_ID

    def test_duplicada_retorna_409(self, client, session) -> None:
        session.scalar.side_effect = [AGENT_ID, 3]
        session.commit.side_effect = IntegrityError("x", {}, Exception())

        response = client.post(
            f"/api/v1/agents/{AGENT_ID}/urgency-keywords", json={"keyword": "despejo"}
        )

        assert response.status_code == 409
        session.rollback.assert_awaited_once()

    def test_limite_retorna_409(self, client, session) -> None:
        session.scalar.side_effect = [AGENT_ID, MAX_KEYWORDS_PER_AGENT]

        response = client.post(
            f"/api/v1/agents/{AGENT_ID}/urgency-keywords", json={"keyword": "despejo"}
        )

        assert response.status_code == 409
        session.add.assert_not_called()

    def test_vazia_retorna_422(self, client) -> None:
        response = client.post(
            f"/api/v1/agents/{AGENT_ID}/urgency-keywords", json={"keyword": "   "}
        )
        assert response.status_code == 422

    def test_remove_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = AGENT_ID
        session.execute.return_value = SimpleNamespace(rowcount=0)

        response = client.delete(f"/api/v1/agents/{AGENT_ID}/urgency-keywords/{uuid.uuid4()}")

        assert response.status_code == 404

    def test_restaura_padrao(self, client, session) -> None:
        session.scalar.return_value = AGENT_ID
        session.scalars.side_effect = [
            [normalize("despejo")],
            [_keyword("despejo")],
        ]

        response = client.post(f"/api/v1/agents/{AGENT_ID}/urgency-keywords/restore-defaults")

        assert response.status_code == 200
        session.commit.assert_awaited_once()

    def test_restaura_padrao_respeita_limite(self, client, session) -> None:
        session.scalar.return_value = AGENT_ID
        session.scalars.return_value = [f"personalizada-{index}" for index in range(100)]

        response = client.post(f"/api/v1/agents/{AGENT_ID}/urgency-keywords/restore-defaults")

        assert response.status_code == 409
        session.commit.assert_not_awaited()


def _execute_with(*, scalar_one=None, rows=None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_one
    result.scalars.return_value.all.return_value = rows or []
    result.all.return_value = []
    return result


class TestConversationUrgencyRoutes:
    def test_marca_como_resolvida(self, client, session) -> None:
        conversation = _conversation(
            urgent_since=datetime.now(UTC), urgent_reason="x", urgent_source="agent"
        )
        session.execute.return_value = _execute_with()
        session.scalar.side_effect = [conversation, None, None, None]

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}/urgency", json={"urgent": False}
        )

        assert response.status_code == 200
        assert response.json()["urgent_since"] is None
        session.commit.assert_awaited_once()

    def test_marca_manualmente(self, client, session) -> None:
        conversation = _conversation()
        session.execute.return_value = _execute_with()
        session.scalar.side_effect = [conversation, None, None, None]

        response = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}/urgency", json={"urgent": True}
        )

        assert response.status_code == 200
        assert response.json()["urgent_source"] == "manual"

    def test_filtro_urgentes_na_listagem(self, client, session) -> None:
        session.execute.return_value = _execute_with(rows=[])

        response = client.get("/api/v1/conversations?urgent=true")

        assert response.status_code == 200
        statement = str(session.execute.await_args_list[0].args[0])
        assert "conversations.urgent_since IS NOT NULL" in statement

    def test_contagem_de_urgentes(self, client, session) -> None:
        session.scalar.return_value = 4

        response = client.get("/api/v1/conversations/urgent-count")

        assert response.status_code == 200
        assert response.json() == {"count": 4}
        statement = str(session.scalar.await_args.args[0])
        assert "urgent_since IS NOT NULL" in statement
        assert "is_test IS false" in statement
