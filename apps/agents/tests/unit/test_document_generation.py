from unittest.mock import MagicMock

import httpx
import pytest

import clients.document_generation as document_generation_module
from clients.document_generation import DocumentGenerationError, generate_pdf


class FakeAsyncClient:
    """Substitui httpx.AsyncClient, respondendo por endpoint chamado."""

    responses: dict
    calls: list

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        FakeAsyncClient.calls.append((url, kwargs))
        endpoint = url.rsplit("/", 1)[-1]
        return FakeAsyncClient.responses[endpoint]


def _ok_response(
    json_body: dict | None = None, content: bytes | None = None, text: str | None = None
) -> MagicMock:
    response = MagicMock()
    response.raise_for_status.return_value = None
    if json_body is not None:
        response.json.return_value = json_body
    if content is not None:
        response.content = content
    if text is not None:
        response.text = text
    return response


@pytest.fixture(autouse=True)
def fake_httpx(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr(document_generation_module.httpx, "AsyncClient", FakeAsyncClient)


async def test_generate_pdf_encadeia_as_3_chamadas():
    FakeAsyncClient.responses = {
        "make_multa_llm": _ok_response(json_body={"multa": "texto da multa"}),
        "make_latex": _ok_response(text="\\documentclass..."),
        "compile_latex": _ok_response(content=b"%PDF-1.4 conteudo"),
    }

    pdf_bytes = await generate_pdf("multa", "campo: valor")

    assert pdf_bytes == b"%PDF-1.4 conteudo"
    called_endpoints = [url.rsplit("/", 1)[-1] for url, _ in FakeAsyncClient.calls]
    assert called_endpoints == ["make_multa_llm", "make_latex", "compile_latex"]
    # A cadeia repassa o texto redigido/latex de uma etapa pra outra.
    _, latex_kwargs = FakeAsyncClient.calls[1]
    assert latex_kwargs["json"] == {"text": "texto da multa"}
    _, compile_kwargs = FakeAsyncClient.calls[2]
    assert compile_kwargs["content"] == b"\\documentclass..."


async def test_generate_pdf_usa_endpoint_certo_por_tipo():
    FakeAsyncClient.responses = {
        "make_contrato_llm": _ok_response(json_body={"contract": "texto"}),
        "make_latex": _ok_response(text="latex"),
        "compile_latex": _ok_response(content=b"pdf"),
    }

    await generate_pdf("contrato", "campo: valor")

    called_endpoints = [url.rsplit("/", 1)[-1] for url, _ in FakeAsyncClient.calls]
    assert called_endpoints[0] == "make_contrato_llm"


async def test_erro_http_na_redacao_vira_document_generation_error():
    response = MagicMock()
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "erro", request=MagicMock(), response=MagicMock()
    )
    FakeAsyncClient.responses = {"make_aviso_llm": response}

    with pytest.raises(DocumentGenerationError):
        await generate_pdf("aviso", "campo: valor")


async def test_resposta_sem_campo_esperado_vira_document_generation_error():
    FakeAsyncClient.responses = {
        "make_oficio_llm": _ok_response(json_body={"campo_errado": "texto"}),
    }

    with pytest.raises(DocumentGenerationError):
        await generate_pdf("oficio", "campo: valor")


async def test_pdf_vazio_vira_document_generation_error():
    FakeAsyncClient.responses = {
        "make_edital_convocacao_llm": _ok_response(json_body={"edital": "texto"}),
        "make_latex": _ok_response(text="latex"),
        "compile_latex": _ok_response(content=b""),
    }

    with pytest.raises(DocumentGenerationError):
        await generate_pdf("edital_convocacao", "campo: valor")


async def test_make_latex_vazio_vira_document_generation_error():
    FakeAsyncClient.responses = {
        "make_aviso_llm": _ok_response(json_body={"aviso": "texto"}),
        "make_latex": _ok_response(text=""),
    }

    with pytest.raises(DocumentGenerationError):
        await generate_pdf("aviso", "campo: valor")
