"""Validation for a tenant's private, single-page A4 PDF letterhead."""

import io

from pypdf import PdfReader


class InvalidLetterheadError(ValueError):
    pass


def validate_letterhead(data: bytes) -> None:
    if not data.startswith(b"%PDF-"):
        raise InvalidLetterheadError("Envie um arquivo PDF válido.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or len(reader.pages) != 1:
            raise InvalidLetterheadError("O timbrado precisa ter uma página e não pode ter senha.")
        page = reader.pages[0]
        if page.get("/Annots") or any(
            key in reader.trailer["/Root"] for key in ("/OpenAction", "/AcroForm")
        ):
            raise InvalidLetterheadError(
                "O timbrado não pode conter formulários ou ações interativas."
            )
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        if abs(width - 595.28) > 3 or abs(height - 841.89) > 3:
            raise InvalidLetterheadError("O timbrado deve estar em A4 vertical.")
    except InvalidLetterheadError:
        raise
    except Exception as exc:
        raise InvalidLetterheadError("Não foi possível ler o PDF enviado.") from exc
