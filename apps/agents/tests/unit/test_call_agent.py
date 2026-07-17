from types import SimpleNamespace

from services.call_agent import sum_usage_breakdown


def _ai(usage: dict | None):
    return SimpleNamespace(type="ai", usage_metadata=usage, content="resposta")


def _human():
    return SimpleNamespace(type="human", content="pergunta")


def test_soma_input_output_e_total_das_mensagens_de_ia():
    messages = [
        _human(),
        _ai({"input_tokens": 70, "output_tokens": 30, "total_tokens": 100}),
        _ai({"input_tokens": 200, "output_tokens": 50, "total_tokens": 250}),
    ]

    assert sum_usage_breakdown(messages) == {
        "input_tokens": 270,
        "output_tokens": 80,
        "total_tokens": 350,
    }


def test_mensagem_de_ia_sem_usage_conta_zero():
    messages = [_ai(None), _ai({"input_tokens": 60, "output_tokens": 20, "total_tokens": 80})]

    assert sum_usage_breakdown(messages) == {
        "input_tokens": 60,
        "output_tokens": 20,
        "total_tokens": 80,
    }


def test_mensagem_sem_atributo_usage_metadata_nao_quebra():
    messages = [SimpleNamespace(type="ai", content="x"), _human()]

    assert sum_usage_breakdown(messages) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


def test_usage_sem_chaves_de_input_output_usa_zero():
    messages = [_ai({"total_tokens": 40})]

    assert sum_usage_breakdown(messages) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 40,
    }
