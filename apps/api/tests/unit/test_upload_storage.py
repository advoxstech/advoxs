from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.upload_storage import UploadTooLargeError, read_upload_limited


async def test_leitura_limitada_aceita_arquivo_exatamente_no_limite() -> None:
    upload = SimpleNamespace(read=AsyncMock(side_effect=[b"abcd", b""]))

    assert await read_upload_limited(upload, 4) == b"abcd"


async def test_leitura_limitada_para_no_primeiro_byte_excedente() -> None:
    upload = SimpleNamespace(read=AsyncMock(side_effect=[b"abcd", b"e", b"restante"]))

    with pytest.raises(UploadTooLargeError):
        await read_upload_limited(upload, 4)

    assert upload.read.await_count == 2
