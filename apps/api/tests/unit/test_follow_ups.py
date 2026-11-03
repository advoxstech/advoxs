import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.api.v1.follow_ups as follow_ups_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.whatsapp import WhatsAppNetworkError
from app.core.queue import get_arq_pool
from app.main import app
from app.schemas.follow_ups import FollowUpCreate, FollowUpParam
from app.services.follow_ups import (
    parse_template,
    render_template,
    render_text,
    resolve_params,
)

TENANT_ID = uuid.uuid4()


def _raw_template(**overrides) -> dict:
    raw = {
        "name": "retomada",
        "language": "pt_BR",
        "status": "APPROVED",
        "category": "MARKETING",
        "components": [
            {"type": "BODY", "text": "Olá, {{1}}. Aqui é do escritório {{2}}. Podemos continuar?"},
            {"type": "FOOTER", "text": "Advoxs"},
        ],
    }
    raw.update(overrides)
    return raw


class TestParseTemplate:
    def test_modelo_simples_suportado_com_variaveis_em_ordem(self) -> None:
        parsed = parse_template(_raw_template())

        assert parsed["supported"] is True
        assert parsed["param_names"] == ["1", "2"]
        assert parsed["parameter_format"] == "POSITIONAL"
        assert parsed["footer_text"] == "Advoxs"

    def test_variaveis_nomeadas_sem_repeticao(self) -> None:
        parsed = parse_template(
            _raw_template(
                parameter_format="NAMED",
                components=[{"type": "BODY", "text": "{{ nome }}, {{escritorio}} e {{nome}}"}],
            )
        )

        assert parsed["parameter_format"] == "NAMED"
        assert parsed["param_names"] == ["nome", "escritorio"]

    @pytest.mark.parametrize(
        ("components", "category"),
        [
            ([{"type": "HEADER", "format": "IMAGE"}, {"type": "BODY", "text": "Oi"}], "MARKETING"),
            ([{"type": "HEADER", "text": "Oi {{1}}"}, {"type": "BODY", "text": "Oi"}], "MARKETING"),
            (
                [
                    {"type": "BODY", "text": "Oi"},
                    {"type": "BUTTONS", "buttons": [{"type": "URL", "url": "https://a/{{1}}"}]},
                ],
                "MARKETING",
            ),
            (
                [
                    {"type": "BODY", "text": "Oi"},
                    {"type": "BUTTONS", "buttons": [{"type": "COPY_CODE"}]},
                ],
                "MARKETING",
            ),
            ([{"type": "BODY", "text": "Seu código"}], "AUTHENTICATION"),
            ([{"type": "HEADER", "text": "Sem corpo"}], "UTILITY"),
            ([{"type": "CAROUSEL"}, {"type": "BODY", "text": "Oi"}], "MARKETING"),
        ],
    )
    def test_formatos_nao_suportados_trazem_motivo(self, components, category) -> None:
        parsed = parse_template(_raw_template(components=components, category=category))

        assert parsed["supported"] is False
        assert parsed["unsupported_reason"]

    def test_botoes_simples_e_cabecalho_fixo_sao_suportados(self) -> None:
        parsed = parse_template(
            _raw_template(
                components=[
                    {"type": "HEADER", "format": "TEXT", "text": "Escritório"},
                    {"type": "BODY", "text": "Oi {{1}}"},
                    {
                        "type": "BUTTONS",
                        "buttons": [
                            {"type": "QUICK_REPLY", "text": "Sim"},
                            {"type": "URL", "url": "https://advoxs.com.br"},
                        ],
                    },
                ]
            )
        )

        assert parsed["supported"] is True
        assert parsed["header_text"] == "Escritório"


class TestRender:
    def test_nome_do_contato_com_fallback(self) -> None:
        params = [
            {"source": "contact_name", "value": "cliente"},
            {"source": "text", "value": "Silva Advogados"},
        ]

        assert resolve_params(params, "Maria") == ["Maria", "Silva Advogados"]
        assert resolve_params(params, "  ") == ["cliente", "Silva Advogados"]

    def test_render_template_junta_cabecalho_corpo_e_rodape(self) -> None:
        parsed = parse_template(_raw_template())

        assert render_template(parsed, ["Maria", "Silva"]) == (
            "Olá, Maria. Aqui é do escritório Silva. Podemos continuar?\n\nAdvoxs"
        )

    def test_render_text_substitui_nome(self) -> None:
        assert render_text("Oi {{nome}}!", "João") == "Oi João!"
        assert render_text("Oi {{nome}}!", "") == "Oi cliente!"


class TestSchema:
    def test_exige_destinatario(self) -> None:
        with pytest.raises(ValidationError):
            FollowUpCreate(text="oi")

    def test_variavel_com_quebra_de_linha_e_recusada(self) -> None:
        with pytest.raises(ValidationError):
            FollowUpParam(value="linha 1\nlinha 2")


# ---------------------------------------------------------------- rotas


def _number(provider: str = "meta") -> SimpleNamespace:
    return SimpleNamespace(
        provider=provider,
        status="connected",
        waba_id="waba-1" if provider == "meta" else None,
        access_token_encrypted="cifrado" if provider == "meta" else None,
    )


def _contact(name: str, phone: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), name=name, phone_number=phone)


def _scalars(items: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = items
    return result


@pytest.fixture
def session():
    mock = AsyncMock()
    mock.add = MagicMock()
    mock.add_all = MagicMock()

    async def refresh(obj, *args):
        obj.created_at = datetime.now(UTC)

    mock.refresh.side_effect = refresh
    return mock


@pytest.fixture
def arq():
    return AsyncMock()


def _client(session, arq, role: str = "admin") -> TestClient:
    async def override_ctx():
        return TenantContext(user_id=uuid.uuid4(), tenant_id=TENANT_ID, role=role, name="Ana")

    async def override_session():
        yield session

    async def override_arq():
        return arq

    app.dependency_overrides[get_current_tenant] = override_ctx
    app.dependency_overrides[get_tenant_session] = override_session
    app.dependency_overrides[get_arq_pool] = override_arq
    return TestClient(app)


@pytest.fixture
def client(session, arq):
    yield _client(session, arq)
    app.dependency_overrides.clear()


@pytest.fixture
def templates(monkeypatch):
    fetch = AsyncMock(return_value=[_raw_template()])
    monkeypatch.setattr(follow_ups_module, "fetch_message_templates", fetch)
    monkeypatch.setattr(follow_ups_module, "decrypt_access_token", lambda value: "token")
    return fetch


META_BODY = {
    "contact_ids": [],
    "template_name": "retomada",
    "language": "pt_BR",
    "params": [
        {"source": "contact_name", "value": "cliente"},
        {"source": "text", "value": "Silva Advogados"},
    ],
}


class TestCreateFollowUp:
    def test_sem_numero_conectado_retorna_409(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post(
            "/api/v1/follow-ups", json={**META_BODY, "tag_ids": [str(uuid.uuid4())]}
        )

        assert response.status_code == 409

    def test_advogado_nao_envia(self, session, arq) -> None:
        try:
            response = _client(session, arq, role="lawyer").post(
                "/api/v1/follow-ups", json={"text": "oi", "tag_ids": [str(uuid.uuid4())]}
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 403

    def test_zapi_cria_lote_sem_repetir_numero_e_enfileira(self, client, session, arq) -> None:
        maria = _contact("Maria", "5579999990000")
        repetido = _contact("", "5579999990000")
        joao = _contact("João", "5579888880000")
        session.scalar.return_value = _number("zapi")
        session.scalars.return_value = _scalars([maria, repetido, joao])

        response = client.post(
            "/api/v1/follow-ups",
            json={
                "contact_ids": [str(maria.id), str(repetido.id), str(joao.id)],
                "text": "  Oi {{nome}}, podemos continuar?  ",
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["total"] == 2 and body["pending"] == 2
        assert body["created_by_name"] == "Ana"
        batch = session.add.call_args.args[0]
        assert batch.provider == "zapi" and batch.message_text == "Oi {{nome}}, podemos continuar?"
        recipients = list(session.add_all.call_args.args[0])
        assert sorted(r.phone for r in recipients) == ["5579888880000", "5579999990000"]
        arq.enqueue_job.assert_awaited_once()
        assert arq.enqueue_job.call_args.kwargs["_job_id"] == f"follow-up:{batch.id}"

    def test_zapi_exige_texto(self, client, session) -> None:
        session.scalar.return_value = _number("zapi")

        response = client.post(
            "/api/v1/follow-ups", json={"contact_ids": [str(uuid.uuid4())], "text": "  "}
        )

        assert response.status_code == 422

    def test_meta_grava_snapshot_do_modelo(self, client, session, templates) -> None:
        maria = _contact("Maria", "5579999990000")
        session.scalar.return_value = _number("meta")
        session.scalars.return_value = _scalars([maria])

        response = client.post(
            "/api/v1/follow-ups", json={**META_BODY, "contact_ids": [str(maria.id)]}
        )

        assert response.status_code == 201, response.text
        batch = session.add.call_args.args[0]
        assert batch.template["name"] == "retomada"
        assert batch.template["param_names"] == ["1", "2"]
        assert batch.params[1] == {"source": "text", "value": "Silva Advogados"}
        templates.assert_awaited_once_with("waba-1", "token")

    def test_meta_recusa_modelo_nao_aprovado(self, client, session, templates) -> None:
        templates.return_value = [_raw_template(status="PAUSED")]
        session.scalar.return_value = _number("meta")

        response = client.post(
            "/api/v1/follow-ups", json={**META_BODY, "contact_ids": [str(uuid.uuid4())]}
        )

        assert response.status_code == 422
        assert "aprovado" in response.json()["detail"]

    def test_meta_exige_todas_as_variaveis(self, client, session, templates) -> None:
        session.scalar.return_value = _number("meta")

        response = client.post(
            "/api/v1/follow-ups",
            json={**META_BODY, "contact_ids": [str(uuid.uuid4())], "params": []},
        )

        assert response.status_code == 422

    def test_meta_indisponivel_retorna_502(self, client, session, templates) -> None:
        templates.side_effect = WhatsAppNetworkError("Falha de rede")
        session.scalar.return_value = _number("meta")

        response = client.post(
            "/api/v1/follow-ups", json={**META_BODY, "contact_ids": [str(uuid.uuid4())]}
        )

        assert response.status_code == 502

    def test_contato_de_outro_escritorio_e_recusado(self, client, session) -> None:
        session.scalar.return_value = _number("zapi")
        session.scalars.return_value = _scalars([])

        response = client.post(
            "/api/v1/follow-ups", json={"contact_ids": [str(uuid.uuid4())], "text": "Oi"}
        )

        assert response.status_code == 422

    def test_limite_de_100_destinatarios(self, client, session) -> None:
        contacts = [_contact("", f"55799{i:08d}") for i in range(101)]
        session.scalar.return_value = _number("zapi")
        session.scalars.return_value = _scalars(contacts)

        response = client.post(
            "/api/v1/follow-ups", json={"tag_ids": [str(uuid.uuid4())], "text": "Oi"}
        )

        assert response.status_code == 422
        assert "100" in response.json()["detail"]

    def test_conversa_de_teste_nao_entra(self, client, session) -> None:
        session.scalar.return_value = _number("zapi")
        result = MagicMock()
        result.all.return_value = []
        session.execute.return_value = result

        response = client.post(
            "/api/v1/follow-ups",
            json={"conversation_ids": [str(uuid.uuid4())], "text": "Oi"},
        )

        assert response.status_code == 422


class TestListTemplates:
    def test_zapi_nao_tem_modelos(self, client, session) -> None:
        session.scalar.return_value = _number("zapi")

        response = client.get("/api/v1/follow-ups/templates")

        assert response.json() == {"provider": "zapi", "templates": []}

    def test_meta_lista_modelos_com_situacao(self, client, session, templates) -> None:
        templates.return_value = [
            _raw_template(),
            _raw_template(name="velho", status="REJECTED", rejected_reason="PROMOTIONAL"),
        ]
        session.scalar.return_value = _number("meta")

        response = client.get("/api/v1/follow-ups/templates")

        body = response.json()
        assert body["provider"] == "meta"
        assert [t["status"] for t in body["templates"]] == ["APPROVED", "REJECTED"]
        assert body["templates"][1]["rejected_reason"] == "PROMOTIONAL"
