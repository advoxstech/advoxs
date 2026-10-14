import io
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

import app.api.v1.profile as profile_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.core.security import hash_password
from app.main import app
from app.services.profile import InvalidCurrentPasswordError
from app.services.profile import change_password as change_password_service

TENANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
PNG = b"\x89PNG\r\n\x1a\nconteudo"
JPEG = b"\xff\xd8\xffconteudo"


def _tenant(logo: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(name="Escritório Teste", email_contato="a@b.com", logo_filename=logo)


def _user() -> SimpleNamespace:
    return SimpleNamespace(name="Fulano", email="fulano@b.com")


@pytest.fixture
def session():
    mock = AsyncMock()
    return mock


@pytest.fixture
def client(session):
    async def override_tenant():
        return TenantContext(user_id=USER_ID, tenant_id=TENANT_ID, role="admin")

    async def override_session():
        yield session

    app.dependency_overrides[get_current_tenant] = override_tenant
    app.dependency_overrides[get_tenant_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestGetProfile:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).get("/api/v1/profile")
        assert response.status_code == 401

    def test_retorna_dados_do_tenant_e_do_usuario(self, client, session) -> None:
        session.get = AsyncMock(side_effect=[_tenant(), _user()])

        response = client.get("/api/v1/profile")

        assert response.status_code == 200
        body = response.json()
        assert body["tenant_name"] == "Escritório Teste"
        assert body["has_logo"] is False
        assert body["user_name"] == "Fulano"

    def test_has_logo_true_quando_tenant_tem_logo(self, client, session) -> None:
        session.get = AsyncMock(side_effect=[_tenant(logo="abc.png"), _user()])

        response = client.get("/api/v1/profile")

        assert response.json()["has_logo"] is True


class TestUpdateProfile:
    def test_atualiza_o_nome(self, client, session, monkeypatch) -> None:
        update = AsyncMock(return_value=_tenant())
        monkeypatch.setattr(profile_module, "update_tenant_name", update)
        session.get = AsyncMock(return_value=_user())

        response = client.patch("/api/v1/profile", json={"tenant_name": "Novo Nome"})

        assert response.status_code == 200
        update.assert_awaited_once()
        assert update.await_args.args[1] == TENANT_ID
        assert update.await_args.args[2] == "Novo Nome"

    def test_nome_vazio_retorna_422(self, client) -> None:
        response = client.patch("/api/v1/profile", json={"tenant_name": ""})
        assert response.status_code == 422


class TestChangePasswordRoute:
    def test_senha_atual_errada_retorna_400(self, client, monkeypatch) -> None:
        change = AsyncMock(side_effect=InvalidCurrentPasswordError("Senha atual incorreta"))
        monkeypatch.setattr(profile_module, "change_password", change)

        response = client.post(
            "/api/v1/profile/password",
            json={"current_password": "errada", "new_password": "nova12345"},
        )

        assert response.status_code == 400

    def test_sucesso_retorna_204(self, client, monkeypatch) -> None:
        change = AsyncMock()
        monkeypatch.setattr(profile_module, "change_password", change)

        response = client.post(
            "/api/v1/profile/password",
            json={"current_password": "certa", "new_password": "nova12345"},
        )

        assert response.status_code == 204

    def test_senha_nova_curta_retorna_422(self, client) -> None:
        response = client.post(
            "/api/v1/profile/password",
            json={"current_password": "certa", "new_password": "curta"},
        )
        assert response.status_code == 422


class TestChangePasswordService:
    async def test_incrementa_versao_da_sessao(self, session) -> None:
        user = SimpleNamespace(password_hash=hash_password("senha-atual"), session_version=3)
        session.get.return_value = user

        await change_password_service(session, USER_ID, "senha-atual", "senha-nova")

        assert user.session_version == 4
        session.commit.assert_awaited_once()


class TestUploadLogo:
    def test_extensao_nao_suportada_retorna_400(self, client, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(profile_module.settings, "logo_upload_dir", str(tmp_path))

        response = client.post(
            "/api/v1/profile/logo",
            files={"file": ("logo.gif", b"fake-gif-bytes", "image/gif")},
        )

        assert response.status_code == 400

    def test_arquivo_grande_retorna_413(self, client, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(profile_module.settings, "logo_upload_dir", str(tmp_path))
        monkeypatch.setattr(profile_module.settings, "logo_max_file_size_bytes", 10)

        response = client.post(
            "/api/v1/profile/logo",
            files={"file": ("logo.png", b"0123456789ABC", "image/png")},
        )

        assert response.status_code == 413

    def test_upload_valido_grava_o_arquivo_e_atualiza_o_tenant(
        self, client, session, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.setattr(profile_module.settings, "logo_upload_dir", str(tmp_path))
        tenant = _tenant()
        user = _user()

        async def fake_get(model, _id):
            return tenant if model.__name__ == "Tenant" else user

        session.get = fake_get

        response = client.post(
            "/api/v1/profile/logo",
            files={"file": ("logo.png", PNG, "image/png")},
        )

        assert response.status_code == 200
        assert response.json()["user_name"] == "Fulano"
        assert tenant.logo_filename == f"{TENANT_ID}.png"
        assert (tmp_path / f"{TENANT_ID}.png").read_bytes() == PNG
        session.commit.assert_awaited_once()

    def test_upload_com_extensao_diferente_remove_o_arquivo_anterior(
        self, client, session, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.setattr(profile_module.settings, "logo_upload_dir", str(tmp_path))
        (tmp_path / f"{TENANT_ID}.png").write_bytes(b"logo-antiga")
        tenant = _tenant(logo=f"{TENANT_ID}.png")
        user = _user()

        async def fake_get(model, _id):
            return tenant if model.__name__ == "Tenant" else user

        session.get = fake_get

        response = client.post(
            "/api/v1/profile/logo",
            files={"file": ("logo.jpg", JPEG, "image/jpeg")},
        )

        assert response.status_code == 200
        assert tenant.logo_filename == f"{TENANT_ID}.jpg"
        assert not (tmp_path / f"{TENANT_ID}.png").exists()
        assert (tmp_path / f"{TENANT_ID}.jpg").read_bytes() == JPEG

    def test_imagem_falsa_retorna_400(self, client, session, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(profile_module.settings, "logo_upload_dir", str(tmp_path))

        response = client.post(
            "/api/v1/profile/logo",
            files={"file": ("logo.png", b"nao e imagem", "image/png")},
        )

        assert response.status_code == 400


class TestLetterhead:
    @staticmethod
    def pdf(pages: int = 1) -> bytes:
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=595.28, height=841.89)
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    def test_upload_preview_remove_are_scoped_to_office(
        self, client, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.setattr(profile_module.settings, "letterhead_dir", str(tmp_path))
        pdf = self.pdf()
        response = client.post(
            "/api/v1/profile/letterhead",
            files={"file": ("timbrado.pdf", pdf, "application/pdf")},
        )
        assert response.status_code == 200
        assert client.get("/api/v1/profile/letterhead").content == pdf

        other_office = uuid.uuid4()

        async def other_context():
            return TenantContext(user_id=USER_ID, tenant_id=other_office, role="admin")

        app.dependency_overrides[get_current_tenant] = other_context
        assert client.get("/api/v1/profile/letterhead").status_code == 404
        assert client.delete("/api/v1/profile/letterhead").status_code == 204
        assert (tmp_path / f"{TENANT_ID}.pdf").read_bytes() == pdf

        app.dependency_overrides[get_current_tenant] = lambda: TenantContext(
            user_id=USER_ID, tenant_id=TENANT_ID, role="admin"
        )
        assert client.delete("/api/v1/profile/letterhead").status_code == 204
        assert client.get("/api/v1/profile/letterhead").status_code == 404

    def test_rejects_multi_page_and_oversized_pdf(self, client, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(profile_module.settings, "letterhead_dir", str(tmp_path))
        response = client.post(
            "/api/v1/profile/letterhead",
            files={"file": ("pages.pdf", self.pdf(2), "application/pdf")},
        )
        assert response.status_code == 400
        response = client.post(
            "/api/v1/profile/letterhead",
            files={"file": ("invalid.pdf", b"not a PDF", "application/pdf")},
        )
        assert response.status_code == 400
        monkeypatch.setattr(profile_module.settings, "letterhead_max_file_size_bytes", 10)
        response = client.post(
            "/api/v1/profile/letterhead",
            files={"file": ("big.pdf", self.pdf(), "application/pdf")},
        )
        assert response.status_code == 413

    def test_lawyer_cannot_replace_letterhead(self, client, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(profile_module.settings, "letterhead_dir", str(tmp_path))

        async def lawyer_context():
            return TenantContext(user_id=USER_ID, tenant_id=TENANT_ID, role="lawyer")

        app.dependency_overrides[get_current_tenant] = lawyer_context
        response = client.post(
            "/api/v1/profile/letterhead",
            files={"file": ("timbrado.pdf", self.pdf(), "application/pdf")},
        )
        assert response.status_code == 403
        assert not (tmp_path / f"{TENANT_ID}.pdf").exists()


class TestGetLogo:
    def test_sem_logo_retorna_404(self, client, session) -> None:
        session.get = AsyncMock(return_value=_tenant(logo=None))

        response = client.get("/api/v1/profile/logo")

        assert response.status_code == 404

    def test_com_logo_retorna_o_arquivo(self, client, session, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(profile_module.settings, "logo_upload_dir", str(tmp_path))
        (tmp_path / f"{TENANT_ID}.png").write_bytes(b"fake-png-bytes")
        session.get = AsyncMock(return_value=_tenant(logo=f"{TENANT_ID}.png"))

        response = client.get("/api/v1/profile/logo")

        assert response.status_code == 200
        assert response.content == b"fake-png-bytes"
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "no-store"
