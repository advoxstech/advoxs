from app.core.db import engine, get_session, get_system_session, system_engine


def test_engine_e_system_engine_sao_conexoes_distintas() -> None:
    assert engine is not system_engine
    assert str(engine.url) != str(system_engine.url)


def test_engine_usa_app_database_url() -> None:
    assert engine.url.username == "advoxs_app"
    assert engine.url.database == "advoxs"


def test_system_engine_usa_system_database_url() -> None:
    assert system_engine.url.username == "advoxs_system"


def test_get_session_e_get_system_session_sao_funcoes_distintas() -> None:
    assert get_session is not get_system_session
