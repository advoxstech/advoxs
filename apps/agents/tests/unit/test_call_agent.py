from types import SimpleNamespace

from services.call_agent import sum_usage_tokens


def _ai(total_tokens: int | None):
    usage = {"total_tokens": total_tokens} if total_tokens is not None else None
    return SimpleNamespace(type="ai", usage_metadata=usage, content="resposta")


def _human():
    return SimpleNamespace(type="human", content="pergunta")


def test_soma_tokens_de_todas_as_mensagens_de_ia():
    messages = [_human(), _ai(100), _ai(250)]

    assert sum_usage_tokens(messages) == 350


def test_mensagem_de_ia_sem_usage_conta_zero():
    messages = [_ai(None), _ai(80)]

    assert sum_usage_tokens(messages) == 80


def test_mensagem_sem_atributo_usage_metadata_nao_quebra():
    messages = [SimpleNamespace(type="ai", content="x"), _human()]

    assert sum_usage_tokens(messages) == 0
