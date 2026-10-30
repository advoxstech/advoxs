import httpx
import pytest

from app.clients import contact_media as media


def client(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        media.httpx,
        "AsyncClient",
        lambda **kw: original(
            transport=httpx.MockTransport(handler),
            **kw,
        ),
    )


async def test_meta_download_resolves_id_and_limits_download(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/attachment"})
        return httpx.Response(200, content=b"PDF")

    client(monkeypatch, handler)
    assert await media.download_contact_media("12345", "meta", "secret") == b"PDF"
    assert len(requests) == 2
    assert requests[1].headers["authorization"] == "Bearer secret"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "https://evil.test/",
        "https://z-api.io.evil.test/",
        "https://z-api.io:8443/a",
    ],
)
async def test_untrusted_url_never_receives_provider_credentials(monkeypatch, url):
    requests = []
    client(monkeypatch, lambda request: requests.append(request))
    with pytest.raises(media.ContactMediaError):
        await media.download_contact_media(url, "zapi", "secret")
    assert requests == []


async def test_storage_download_does_not_leak_zapi_token(monkeypatch):
    def handler(request):
        assert "client-token" not in request.headers
        return httpx.Response(200, content=b"file")

    client(monkeypatch, handler)
    assert (
        await media.download_contact_media(
            "https://storage.googleapis.com/bucket/file", "zapi", "secret"
        )
        == b"file"
    )


@pytest.mark.parametrize("headers", [{}, {"content-length": "50"}])
async def test_oversized_attachment_is_rejected(monkeypatch, headers):
    monkeypatch.setattr(media, "MAX_MEDIA_BYTES", 4)
    client(monkeypatch, lambda request: httpx.Response(200, content=b"12345", headers=headers))
    with pytest.raises(media.ContactMediaTooLarge):
        await media.download_contact_media("https://z-api.io/file", "zapi", "secret")


async def test_redirects_are_not_followed(monkeypatch):
    client(
        monkeypatch, lambda request: httpx.Response(302, headers={"location": "http://127.0.0.1"})
    )
    with pytest.raises(media.ContactMediaError):
        await media.download_contact_media("https://z-api.io/file", "zapi", "secret")
