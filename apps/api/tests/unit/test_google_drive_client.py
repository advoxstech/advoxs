import logging

import httpx
import pytest

from app.clients.google_drive import DOCX_MIME, DriveError, DriveFile, GoogleDrive
from app.core.config import settings


def metadata(**overrides):
    return {
        "id": "abc",
        "name": "normas.pdf",
        "mimeType": "application/pdf",
        "version": "2",
        "size": "12",
        "capabilities": {"canDownload": True},
        **overrides,
    }


async def test_download_uses_google_authorization_and_does_not_log_token(caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.host == "www.googleapis.com"
        assert request.headers["Authorization"] == "Bearer private-token"
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=b"%PDF-1.4 ok")
        return httpx.Response(200, json=metadata())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        drive = GoogleDrive(http, "private-token")
        file = await drive.metadata("abc")
        assert await drive.download(file) == b"%PDF-1.4 ok"
    assert len(seen) == 3  # metadata, download, versão após download
    assert "private-token" not in caplog.text
    assert all("private-token" not in str(request.url) for request in seen)


async def test_google_document_exported_as_docx():
    def handler(request):
        if request.url.path.endswith("/export"):
            assert request.url.params["mimeType"] == DOCX_MIME
            return httpx.Response(200, content=b"docx-content")
        return httpx.Response(
            200, json=metadata(name="Regras", mimeType="application/vnd.google-apps.document")
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        drive = GoogleDrive(http, "token")
        file = await drive.metadata("abc")
        assert file.filename == "Regras.docx"
        assert file.mime_type == DOCX_MIME
        assert await drive.download(file) == b"docx-content"


@pytest.mark.parametrize(
    "status,expected", [(401, 403), (403, 403), (404, 403), (429, 503), (500, 503)]
)
async def test_google_errors_are_sanitized(status, expected):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="private provider details")
        )
    ) as http:
        with pytest.raises(DriveError) as caught:
            await GoogleDrive(http, "token").metadata("abc")
    assert caught.value.status == expected
    assert "private provider details" not in str(caught.value)


@pytest.mark.parametrize(
    "fields",
    [
        {"trashed": True},
        {"capabilities": {"canDownload": False}},
        {"mimeType": "application/vnd.google-apps.spreadsheet"},
        {"mimeType": "application/vnd.google-apps.shortcut"},
        {"name": "malware.exe"},
    ],
)
async def test_rejects_unavailable_or_unsupported_files(fields):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=metadata(**fields)))
    ) as http:
        with pytest.raises(DriveError):
            await GoogleDrive(http, "token").metadata("abc")


async def test_download_size_bounded_even_without_content_length(monkeypatch):
    monkeypatch.setattr(settings, "kb_max_file_size_bytes", 4)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"more-than-four-bytes")
        )
    ) as http:
        with pytest.raises(DriveError) as caught:
            await GoogleDrive(http, "token").download(
                DriveFile("abc", "x.pdf", "application/pdf", "1", False)
            )
    assert caught.value.status == 413


async def test_edit_during_download_rejected():
    def handler(request):
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=b"%PDF-1.4")
        return httpx.Response(200, json=metadata(version="3"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(DriveError, match="mudou"):
            await GoogleDrive(http, "token").download(
                DriveFile("abc", "x.pdf", "application/pdf", "2", False)
            )


async def test_redirect_does_not_forward_credentials():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://untrusted.example/private"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as http:
        with pytest.raises(DriveError):
            await GoogleDrive(http, "token").download(
                DriveFile("abc", "x.pdf", "application/pdf", "2", False)
            )
    assert len(seen) == 1
