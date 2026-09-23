"""Armazenamento privado e temporário dos PDFs gerados pelos agentes.

O volume não é exposto diretamente. Cada PDF recebe um token aleatório e um
prazo de validade registrados em um arquivo de metadados privado. A rota HTTP
só entrega o documento quando ambos conferem, permitindo que Meta/Z-API façam
o download sem tornar o arquivo permanentemente público.
"""

import asyncio
import hashlib
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from loguru import logger

from clients.document_generation import DocumentGenerationError
from core.safe_logging import safe_error, safe_identifier

GENERATED_DOCUMENTS_DIR = os.getenv("GENERATED_DOCUMENTS_DIR", "/data/generated_documents")
AGENTS_PUBLIC_URL = os.getenv("AGENTS_PUBLIC_URL", "")

RETENTION_HOURS = 24
_CLEANUP_INTERVAL_SECONDS = 60 * 60
_METADATA_VERSION = 1
_SERVICE_STARTED_AT = time.time()


def _ensure_dir() -> None:
    os.makedirs(GENERATED_DOCUMENTS_DIR, exist_ok=True)


def _document_path(doc_id: str) -> Path:
    return Path(GENERATED_DOCUMENTS_DIR) / f"{doc_id}.pdf"


def _metadata_path(doc_id: str) -> Path:
    return Path(GENERATED_DOCUMENTS_DIR) / f"{doc_id}.json"


def _write_atomic(path: Path, content: bytes) -> None:
    """Publica um arquivo completo; nunca deixa conteúdo parcial no caminho final."""
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary_path.open("xb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _load_metadata(doc_id: str) -> dict | None:
    try:
        data = json.loads(_metadata_path(doc_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("version") != _METADATA_VERSION:
        return None
    return data


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_pdf(pdf_bytes: bytes, *, conversation_id: str = "") -> str:
    """Grava PDF e autorização temporária, vinculados à conversa de origem."""
    if not pdf_bytes.startswith(b"%PDF-"):
        raise DocumentGenerationError("Falha ao armazenar o documento gerado.")

    _ensure_dir()
    doc_id = uuid.uuid4().hex
    access_token = secrets.token_urlsafe(32)
    now = int(time.time())
    tenant_id, separator, _contact = conversation_id.partition(":")
    metadata = {
        "version": _METADATA_VERSION,
        "access_token": access_token,
        "created_at": now,
        "expires_at": now + RETENTION_HOURS * 3600,
        "tenant_id": tenant_id if separator else "",
        "conversation_ref": safe_identifier(conversation_id) if conversation_id else "",
        "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
    }

    document_path = _document_path(doc_id)
    metadata_path = _metadata_path(doc_id)
    try:
        _write_atomic(document_path, pdf_bytes)
        _write_atomic(
            metadata_path,
            json.dumps(metadata, ensure_ascii=True, separators=(",", ":")).encode("utf-8"),
        )
    except OSError as exc:
        document_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        logger.error(
            "Falha ao armazenar PDF gerado | doc_id={} error_type={}",
            doc_id,
            safe_error(exc),
        )
        raise DocumentGenerationError("Falha ao armazenar o documento gerado.") from exc

    logger.info("PDF gerado salvo | doc_id={} tamanho_bytes={}", doc_id, len(pdf_bytes))
    return doc_id


def build_public_url(doc_id: str) -> str:
    """Monta a URL temporária usando o token persistido junto ao documento."""
    if not AGENTS_PUBLIC_URL:
        raise DocumentGenerationError(
            "Falha ao gerar o link do documento: AGENTS_PUBLIC_URL não configurada."
        )
    metadata = _load_metadata(doc_id)
    if metadata is None or not metadata.get("access_token"):
        raise DocumentGenerationError("Falha ao gerar o link temporário do documento.")
    query = urlencode({"token": metadata["access_token"]})
    return f"{AGENTS_PUBLIC_URL.rstrip('/')}/generated-documents/{doc_id}?{query}"


def resolve_path(doc_id: str) -> str | None:
    """Resolve apenas UUIDs válidos dentro do diretório privado."""
    try:
        uuid.UUID(hex=doc_id)
    except ValueError:
        return None
    path = _document_path(doc_id)
    return str(path) if path.is_file() else None


def resolve_authorized_path(doc_id: str, access_token: str | None) -> str | None:
    """Autoriza o download sem revelar se documento, token ou prazo falhou."""
    path = resolve_path(doc_id)
    if path is None:
        return None
    metadata = _load_metadata(doc_id)
    if metadata is None and not access_token:
        # Compatibilidade de deploy: documentos criados pela versão anterior
        # não têm metadados nem token. Eles continuam acessíveis somente pelo
        # restante da retenção original e nunca são renovados. Um PDF novo
        # sem metadados (ex.: processo interrompido entre os arquivos) não é
        # exposto, pois sua data é posterior ao início deste processo.
        try:
            modified_at = os.path.getmtime(path)
        except OSError:
            return None
        is_legacy = modified_at < _SERVICE_STARTED_AT
        is_retained = modified_at + RETENTION_HOURS * 3600 > time.time()
        return path if is_legacy and is_retained else None
    if metadata is None:
        return None
    if not access_token:
        return None
    expected_token = metadata.get("access_token")
    expires_at = metadata.get("expires_at")
    expected_sha256 = metadata.get("sha256")
    if not all(
        (
            isinstance(expected_token, str),
            isinstance(expires_at, int),
            isinstance(expected_sha256, str),
        )
    ):
        return None
    if expires_at <= int(time.time()):
        return None
    if not secrets.compare_digest(access_token, expected_token):
        return None
    try:
        actual_sha256 = _sha256_file(path)
    except OSError:
        return None
    if not secrets.compare_digest(actual_sha256, expected_sha256):
        return None
    return path


def cleanup_old_files(max_age_hours: int = RETENTION_HOURS) -> int:
    """Remove cada PDF expirado e seu metadado como uma única unidade lógica."""
    directory = Path(GENERATED_DOCUMENTS_DIR)
    if not directory.is_dir():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for path in directory.glob("*.pdf"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                path.with_suffix(".json").unlink(missing_ok=True)
                removed += 1
        except OSError as exc:
            logger.warning(
                "Falha ao limpar documento gerado | doc_ref={} erro={}",
                safe_identifier(path.name),
                safe_error(exc),
            )

    # Metadados órfãos não dão acesso a conteúdo, mas também não devem crescer
    # para sempre após uma interrupção ocorrida entre as duas gravações.
    for metadata_path in directory.glob("*.json"):
        try:
            is_old = metadata_path.stat().st_mtime < cutoff
            if not metadata_path.with_suffix(".pdf").exists() and is_old:
                metadata_path.unlink()
        except OSError as exc:
            logger.warning(
                "Falha ao limpar metadado de documento | doc_ref={} erro={}",
                safe_identifier(metadata_path.name),
                safe_error(exc),
            )
    if removed:
        logger.info("Limpeza de documentos gerados | removidos={}", removed)
    return removed


async def start_cleanup_loop() -> None:
    """Remove documentos vencidos a cada hora sem derrubar o serviço."""
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
        try:
            cleanup_old_files()
        except Exception as exc:
            logger.error(
                "Erro inesperado na limpeza de documentos gerados | error_type={}",
                safe_error(exc),
            )
