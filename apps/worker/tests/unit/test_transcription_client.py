import math

import httpx
import pytest

from app.clients import transcription as client
from app.clients.transcription import TranscriptionError, mark_uncertain, transcribe
from app.config import settings


def _lp(token: str, prob: float) -> dict:
    return {"token": token, "logprob": math.log(prob)}


def test_sem_logprobs_devolve_texto_sem_marcas() -> None:
    result = mark_uncertain(" Olá, tudo bem? ", [], 0.5)
    assert result.text == "Olá, tudo bem?"
    assert result.uncertain_spans == []


def test_marca_palavras_incertas_e_junta_adjacentes() -> None:
    logprobs = [
        _lp(" Meu", 0.99),
        _lp(" CPF", 0.98),
        _lp(" é", 0.99),
        _lp(" 123", 0.2),
        _lp("45", 0.3),
        _lp(" 678", 0.1),
        _lp(",", 0.99),
        _lp(" obrigado", 0.97),
    ]
    result = mark_uncertain("Meu CPF é 12345 678, obrigado", logprobs, 0.5)
    assert result.text == "Meu CPF é [?12345 678?], obrigado"
    assert result.uncertain_spans == [{"text": "12345 678", "confidence": 0.1}]


def test_palavra_com_um_token_incerto_inteira_fica_incerta() -> None:
    result = mark_uncertain("Roberto", [_lp(" Ro", 0.9), _lp("berto", 0.3)], 0.5)
    assert result.text == "[?Roberto?]"


def test_valor_monetario_nao_e_dividido() -> None:
    logprobs = [
        _lp(" R", 0.99),
        _lp("$", 0.99),
        _lp(" 1", 0.9),
        _lp(".", 0.9),
        _lp("500", 0.2),
        _lp(",", 0.9),
        _lp("00", 0.9),
    ]
    result = mark_uncertain(" R$ 1.500,00", logprobs, 0.5)
    assert result.text == "R$ [?1.500,00?]"
    assert result.uncertain_spans == [{"text": "1.500,00", "confidence": 0.2}]


def test_cpf_em_varios_tokens_fica_inteiro() -> None:
    logprobs = [_lp(" 123", 0.9), _lp(".", 0.9), _lp("456", 0.1), _lp(".", 0.9), _lp("789", 0.9)]
    result = mark_uncertain(" 123.456.789", logprobs, 0.5)
    assert result.text == "[?123.456.789?]"


def test_palavra_composta_com_hifen() -> None:
    logprobs = [_lp(" segunda", 0.9), _lp("-feira", 0.2)]
    result = mark_uncertain(" segunda-feira", logprobs, 0.5)
    assert result.text == "[?segunda-feira?]"


def test_apostrofo_nao_divide_palavra() -> None:
    logprobs = [_lp(" d", 0.9), _lp("'", 0.9), _lp("água", 0.2)]
    result = mark_uncertain(" d'água", logprobs, 0.5)
    assert result.text == "[?d'água?]"


def test_pontuacao_final_fica_fora_dos_colchetes() -> None:
    logprobs = [_lp(" Roberto", 0.2), _lp(",", 0.99), _lp(" oi", 0.99)]
    result = mark_uncertain(" Roberto, oi", logprobs, 0.5)
    assert result.text == "[?Roberto?], oi"
    assert result.uncertain_spans == [{"text": "Roberto", "confidence": 0.2}]


def test_pontuacao_inicial_fica_fora_dos_colchetes() -> None:
    result = mark_uncertain(' "Roberto"', [_lp(' "', 0.1), _lp("Roberto", 0.2), _lp('"', 0.1)], 0.5)
    assert result.text == '"[?Roberto?]"'


def test_limiar_exato_nao_marca() -> None:
    result = mark_uncertain(" ok", [_lp(" ok", 0.5)], 0.5)
    assert result.text == "ok"
    assert result.uncertain_spans == []


def test_palavra_so_pontuacao_nunca_marcada() -> None:
    result = mark_uncertain(" a ...", [_lp(" a", 0.9), _lp(" ...", 0.1)], 0.5)
    assert result.text == "a ..."
    assert result.uncertain_spans == []


def test_texto_divergente_nao_marca() -> None:
    result = mark_uncertain(" Olá mundo ", [_lp(" Ol", 0.1), _lp("\xc3", 0.1)], 0.5)
    assert result.text == "Olá mundo"
    assert result.uncertain_spans == []


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_transcribe_envia_modelo_idioma_e_logprobs(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.content
        seen["path"] = request.url.path
        return httpx.Response(
            200, json={"text": "Bom dia", "logprobs": [_lp(" Bom", 0.9), _lp(" dia", 0.9)]}
        )

    result = await transcribe(b"OggS...", "audio-1.ogg", transport=_transport(handler))

    assert result.text == "Bom dia"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["path"].endswith("/audio/transcriptions")
    for expected in (b"gpt-4o-transcribe", b'name="language"', b"pt", b"logprobs"):
        assert expected in seen["body"]


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "invalid_format"),
        (429, "provider_unavailable"),
        (500, "provider_unavailable"),
        (401, "provider_unavailable"),
    ],
)
async def test_transcribe_mapeia_status(status, code) -> None:
    transport = _transport(lambda r: httpx.Response(status, json={"error": {"message": "x"}}))
    with pytest.raises(TranscriptionError) as exc:
        await transcribe(b"OggS", "a.ogg", transport=transport)
    assert exc.value.error_code == code


async def test_transcribe_corpo_nao_json() -> None:
    transport = _transport(lambda r: httpx.Response(200, content=b"<html>"))
    with pytest.raises(TranscriptionError) as exc:
        await transcribe(b"OggS", "a.ogg", transport=transport)
    assert exc.value.error_code == "provider_unavailable"


async def test_transcribe_timeout() -> None:
    def handler(request):
        raise httpx.ReadTimeout("lento", request=request)

    with pytest.raises(TranscriptionError) as exc:
        await transcribe(b"OggS", "a.ogg", transport=_transport(handler))
    assert exc.value.error_code == "timeout"


async def test_transcribe_conexao_recusada() -> None:
    def handler(request):
        raise httpx.ConnectError("off", request=request)

    with pytest.raises(TranscriptionError) as exc:
        await transcribe(b"OggS", "a.ogg", transport=_transport(handler))
    assert exc.value.error_code == "provider_unavailable"


async def test_semaforo_respeita_concorrencia(monkeypatch) -> None:
    monkeypatch.setattr(settings, "transcription_max_concurrency", 1)
    monkeypatch.setattr(client, "_semaphore_instance", None)
    assert client._semaphore()._value == 1
