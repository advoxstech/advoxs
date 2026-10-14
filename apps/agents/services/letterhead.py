"""Read the current office letterhead from the shared private volume."""

import os
import uuid
from pathlib import Path

from clients.document_generation import DocumentGenerationError

LETTERHEAD_DIR = os.getenv("LETTERHEAD_DIR", "/data/letterheads")
MAX_LETTERHEAD_BYTES = 5 * 1024 * 1024


def letterhead_for_conversation(conversation_id: str) -> bytes | None:
    tenant_id, separator, _ = conversation_id.partition(":")
    if not separator:
        return None
    try:
        tenant_id = str(uuid.UUID(tenant_id))
    except ValueError:
        return None
    path = Path(LETTERHEAD_DIR) / f"{tenant_id}.pdf"
    try:
        with path.open("rb") as file:
            data = file.read(MAX_LETTERHEAD_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DocumentGenerationError("Não foi possível ler o timbrado do escritório.") from exc
    if len(data) > MAX_LETTERHEAD_BYTES or not data.startswith(b"%PDF-"):
        raise DocumentGenerationError("O timbrado do escritório está inválido.")
    return data
