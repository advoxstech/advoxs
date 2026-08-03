import time
import uuid

import pytest

import services.document_storage as document_storage_module
from clients.document_generation import DocumentGenerationError
from services.document_storage import (
    build_public_url,
    cleanup_old_files,
    resolve_path,
    save_pdf,
)


@pytest.fixture(autouse=True)
def generated_documents_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(document_storage_module, "GENERATED_DOCUMENTS_DIR", str(tmp_path))
    return tmp_path


def test_save_pdf_grava_arquivo_e_devolve_doc_id_valido(generated_documents_dir):
    doc_id = save_pdf(b"%PDF-1.4 conteudo")

    uuid.UUID(hex=doc_id)  # não levanta
    assert (generated_documents_dir / f"{doc_id}.pdf").read_bytes() == b"%PDF-1.4 conteudo"


def test_build_public_url_monta_a_partir_de_agents_public_url(monkeypatch):
    monkeypatch.setattr(document_storage_module, "AGENTS_PUBLIC_URL", "https://agents.exemplo.com")

    url = build_public_url("abc123")

    assert url == "https://agents.exemplo.com/generated-documents/abc123"


def test_build_public_url_remove_barra_final_duplicada(monkeypatch):
    monkeypatch.setattr(document_storage_module, "AGENTS_PUBLIC_URL", "https://agents.exemplo.com/")

    url = build_public_url("abc123")

    assert url == "https://agents.exemplo.com/generated-documents/abc123"


def test_build_public_url_sem_env_levanta_erro(monkeypatch):
    monkeypatch.setattr(document_storage_module, "AGENTS_PUBLIC_URL", "")

    with pytest.raises(DocumentGenerationError):
        build_public_url("abc123")


def test_resolve_path_devolve_none_para_doc_id_invalido(generated_documents_dir):
    assert resolve_path("../../etc/passwd") is None
    assert resolve_path("nao-e-um-uuid") is None


def test_resolve_path_devolve_none_para_arquivo_inexistente(generated_documents_dir):
    doc_id = uuid.uuid4().hex
    assert resolve_path(doc_id) is None


def test_resolve_path_devolve_o_caminho_para_arquivo_existente(generated_documents_dir):
    doc_id = save_pdf(b"conteudo")

    path = resolve_path(doc_id)

    assert path is not None
    assert path.endswith(f"{doc_id}.pdf")


def test_cleanup_old_files_remove_so_arquivos_velhos(generated_documents_dir):
    doc_id_velho = save_pdf(b"velho")
    doc_id_novo = save_pdf(b"novo")
    velho_path = generated_documents_dir / f"{doc_id_velho}.pdf"
    antigo = time.time() - 25 * 3600
    import os

    os.utime(velho_path, (antigo, antigo))

    removidos = cleanup_old_files(max_age_hours=24)

    assert removidos == 1
    assert not velho_path.exists()
    assert (generated_documents_dir / f"{doc_id_novo}.pdf").exists()


def test_cleanup_old_files_sem_diretorio_nao_quebra(monkeypatch, tmp_path):
    monkeypatch.setattr(
        document_storage_module, "GENERATED_DOCUMENTS_DIR", str(tmp_path / "nao-existe")
    )

    assert cleanup_old_files() == 0
