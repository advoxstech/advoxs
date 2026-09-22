"""Validação e armazenamento seguro dos arquivos enviados ao painel."""

import io
import os
import tempfile
import zipfile
from pathlib import Path


class InvalidUploadContentError(ValueError):
    """O conteúdo recebido não corresponde ao formato informado."""


class UnsafeUploadPathError(ValueError):
    """Um caminho calculado saiu do diretório de armazenamento permitido."""


def display_filename(filename: str) -> str:
    """Remove diretórios e caracteres de controle do nome exibido ao usuário."""
    name = filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1].strip()
    name = "".join(char for char in name if char.isprintable())
    if not name or name in {".", ".."}:
        raise InvalidUploadContentError("Nome de arquivo inválido")
    return name[:255]


def validate_upload_content(data: bytes, extension: str) -> None:
    """Confere assinaturas e estruturas mínimas dos formatos aceitos."""
    if extension == ".pdf":
        valid = data.lstrip()[:4] == b"%PDF"
    elif extension == ".png":
        valid = data.startswith(b"\x89PNG\r\n\x1a\n")
    elif extension in {".jpg", ".jpeg"}:
        valid = data.startswith(b"\xff\xd8\xff")
    elif extension == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = set(archive.namelist())
            valid = "[Content_Types].xml" in members and "word/document.xml" in members
        except zipfile.BadZipFile:
            valid = False
    elif extension == ".txt":
        try:
            valid = b"\x00" not in data
            if valid:
                data.decode("utf-8-sig")
        except UnicodeDecodeError:
            valid = False
    else:
        valid = False
    if not valid:
        raise InvalidUploadContentError("Conteúdo não corresponde ao formato do arquivo")


def managed_path(base_dir: str, *parts: str) -> Path:
    """Monta um caminho que obrigatoriamente permanece dentro de ``base_dir``."""
    root = Path(base_dir).resolve()
    candidate = root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root):
        raise UnsafeUploadPathError("Caminho de armazenamento inválido")
    return candidate


def atomic_write(path: Path, data: bytes) -> None:
    """Grava de modo atômico, sem deixar arquivo parcial em caso de falha."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def safe_delete(base_dir: str, *parts: str) -> None:
    managed_path(base_dir, *parts).unlink(missing_ok=True)
