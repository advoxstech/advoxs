import io

import pypdfium2 as pdfium
import pytest

from services.documents import main as documents
from services.documents.main import DocumentoIlegivel, _abrir_pdf


def _pdf(paginas: int) -> bytes:
    pdf = pdfium.PdfDocument.new()
    for _ in range(paginas):
        pdf.new_page(200, 200)
    buffer = io.BytesIO()
    pdf.save(buffer)
    return buffer.getvalue()


def test_pdf_corrompido_tem_motivo_corrompido() -> None:
    with pytest.raises(DocumentoIlegivel) as erro:
        _abrir_pdf(b"%PDF-1.4 isto nao e um pdf")

    assert erro.value.motivo == "corrompido"


def test_pdf_acima_do_limite_de_paginas_tem_motivo_paginas_demais(monkeypatch) -> None:
    monkeypatch.setattr(documents, "MAX_PDF_PAGES", 2)

    with pytest.raises(DocumentoIlegivel) as erro:
        _abrir_pdf(_pdf(3))

    assert erro.value.motivo == "paginas_demais"
    assert "3 páginas" in str(erro.value)
