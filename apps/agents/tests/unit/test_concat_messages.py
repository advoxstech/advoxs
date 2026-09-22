from services.concat_messages import DEFAULT_DEBOUNCE_SECONDS, configured_debounce_seconds


def test_intervalo_padrao_e_doze_segundos(monkeypatch) -> None:
    monkeypatch.delenv("MESSAGE_DEBOUNCE_SECONDS", raising=False)

    assert configured_debounce_seconds() == 12
    assert DEFAULT_DEBOUNCE_SECONDS == 12


def test_intervalo_configuravel_por_ambiente(monkeypatch) -> None:
    monkeypatch.setenv("MESSAGE_DEBOUNCE_SECONDS", "20")

    assert configured_debounce_seconds() == 20


def test_intervalo_invalido_volta_ao_padrao(monkeypatch) -> None:
    monkeypatch.setenv("MESSAGE_DEBOUNCE_SECONDS", "0")

    assert configured_debounce_seconds() == 12
