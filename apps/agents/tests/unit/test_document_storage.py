import json
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

import services.document_storage as document_storage_module
from clients.document_generation import DocumentGenerationError
from services.document_storage import (
    build_public_url,
    cleanup_old_files,
    resolve_authorized_path,
    resolve_path,
    save_pdf,
)


@pytest.fixture(autouse=True)
def generated_documents_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(document_storage_module, "GENERATED_DOCUMENTS_DIR", str(tmp_path))
    return tmp_path


def test_save_pdf_grava_arquivo_e_devolve_doc_id_valido(generated_documents_dir):
    doc_id = save_pdf(b"%PDF-1.4 conteudo", conversation_id="tenant-1:5511999999999")

    uuid.UUID(hex=doc_id)  # não levanta
    assert (generated_documents_dir / f"{doc_id}.pdf").read_bytes() == b"%PDF-1.4 conteudo"
    metadata = json.loads((generated_documents_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
    assert metadata["tenant_id"] == "tenant-1"
    assert "5511999999999" not in json.dumps(metadata)
    assert metadata["expires_at"] > metadata["created_at"]
    assert len(metadata["access_token"]) >= 32


def test_build_public_url_monta_link_temporario(monkeypatch):
    monkeypatch.setattr(document_storage_module, "AGENTS_PUBLIC_URL", "https://agents.exemplo.com")
    doc_id = save_pdf(b"%PDF-1.4")

    url = build_public_url(doc_id)

    parsed = urlsplit(url)
    assert parsed.path == f"/generated-documents/{doc_id}"
    assert len(parse_qs(parsed.query)["token"][0]) >= 32


def test_build_public_url_remove_barra_final_duplicada(monkeypatch):
    monkeypatch.setattr(document_storage_module, "AGENTS_PUBLIC_URL", "https://agents.exemplo.com/")
    doc_id = save_pdf(b"%PDF-1.4")

    url = build_public_url(doc_id)

    assert url.startswith(f"https://agents.exemplo.com/generated-documents/{doc_id}?token=")


def test_build_public_url_sem_env_levanta_erro(monkeypatch):
    monkeypatch.setattr(document_storage_module, "AGENTS_PUBLIC_URL", "")

    with pytest.raises(DocumentGenerationError):
        build_public_url("abc123")


def test_save_pdf_rejeita_conteudo_que_nao_e_pdf(generated_documents_dir):
    with pytest.raises(DocumentGenerationError):
        save_pdf(b"conteudo inesperado")

    assert list(generated_documents_dir.iterdir()) == []


def test_resolve_path_devolve_none_para_doc_id_invalido(generated_documents_dir):
    assert resolve_path("../../etc/passwd") is None
    assert resolve_path("nao-e-um-uuid") is None


def test_resolve_path_devolve_none_para_arquivo_inexistente(generated_documents_dir):
    doc_id = uuid.uuid4().hex
    assert resolve_path(doc_id) is None


def test_resolve_path_devolve_o_caminho_para_arquivo_existente(generated_documents_dir):
    doc_id = save_pdf(b"%PDF-1.4 conteudo")

    path = resolve_path(doc_id)

    assert path is not None
    assert path.endswith(f"{doc_id}.pdf")


def test_resolve_authorized_path_exige_token_correto(generated_documents_dir):
    doc_id = save_pdf(b"%PDF-1.4 conteudo")
    metadata = json.loads((generated_documents_dir / f"{doc_id}.json").read_text(encoding="utf-8"))

    assert resolve_authorized_path(doc_id, None) is None
    assert resolve_authorized_path(doc_id, "token-invalido") is None
    assert resolve_authorized_path(doc_id, metadata["access_token"]) is not None


def test_resolve_authorized_path_preserva_documento_legado_durante_retencao(
    monkeypatch, generated_documents_dir
):
    doc_id = uuid.uuid4().hex
    path = generated_documents_dir / f"{doc_id}.pdf"
    path.write_bytes(b"%PDF-1.4 legado")
    import os

    os.utime(path, (1_000, 1_000))
    monkeypatch.setattr(document_storage_module, "_SERVICE_STARTED_AT", 2_000)
    monkeypatch.setattr(document_storage_module.time, "time", lambda: 1_500)

    assert resolve_authorized_path(doc_id, None) == str(path)


def test_resolve_authorized_path_nao_expoe_pdf_novo_sem_metadados(
    monkeypatch, generated_documents_dir
):
    doc_id = uuid.uuid4().hex
    path = generated_documents_dir / f"{doc_id}.pdf"
    path.write_bytes(b"%PDF-1.4 incompleto")
    import os

    os.utime(path, (2_000, 2_000))
    monkeypatch.setattr(document_storage_module, "_SERVICE_STARTED_AT", 1_000)
    monkeypatch.setattr(document_storage_module.time, "time", lambda: 2_100)

    assert resolve_authorized_path(doc_id, None) is None


def test_resolve_authorized_path_rejeita_link_expirado(monkeypatch, generated_documents_dir):
    monkeypatch.setattr(document_storage_module.time, "time", lambda: 1_000)
    doc_id = save_pdf(b"%PDF-1.4 conteudo")
    metadata = json.loads((generated_documents_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(document_storage_module.time, "time", lambda: metadata["expires_at"])

    assert resolve_authorized_path(doc_id, metadata["access_token"]) is None


def test_resolve_authorized_path_rejeita_pdf_alterado(generated_documents_dir):
    doc_id = save_pdf(b"%PDF-1.4 conteudo")
    metadata = json.loads((generated_documents_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
    (generated_documents_dir / f"{doc_id}.pdf").write_bytes(b"%PDF-1.4 alterado")

    assert resolve_authorized_path(doc_id, metadata["access_token"]) is None


def test_cleanup_old_files_remove_so_arquivos_velhos(generated_documents_dir):
    doc_id_velho = save_pdf(b"%PDF-1.4 velho")
    doc_id_novo = save_pdf(b"%PDF-1.4 novo")
    velho_path = generated_documents_dir / f"{doc_id_velho}.pdf"
    velho_metadata_path = generated_documents_dir / f"{doc_id_velho}.json"
    antigo = time.time() - 25 * 3600
    import os

    os.utime(velho_path, (antigo, antigo))

    removidos = cleanup_old_files(max_age_hours=24)

    assert removidos == 1
    assert not velho_path.exists()
    assert not velho_metadata_path.exists()
    assert (generated_documents_dir / f"{doc_id_novo}.pdf").exists()


def test_cleanup_old_files_sem_diretorio_nao_quebra(monkeypatch, tmp_path):
    monkeypatch.setattr(
        document_storage_module, "GENERATED_DOCUMENTS_DIR", str(tmp_path / "nao-existe")
    )

    assert cleanup_old_files() == 0


def test_cleanup_old_files_redige_nome_e_erro(monkeypatch, generated_documents_dir):
    sensitive_name = "cliente-secreto.pdf"
    (generated_documents_dir / sensitive_name).write_bytes(b"pdf")
    logged: list[str] = []
    original_stat = document_storage_module.Path.stat

    def failing_stat(path, *args, **kwargs):
        if path.name == sensitive_name:
            raise OSError("caminho-secreto")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(document_storage_module.Path, "stat", failing_stat)
    monkeypatch.setattr(
        document_storage_module.logger,
        "warning",
        lambda message, *args: logged.append(message.format(*args)),
    )

    assert cleanup_old_files() == 0
    assert logged
    assert sensitive_name not in logged[0]
    assert "caminho-secreto" not in logged[0]
    assert "OSError" in logged[0]
