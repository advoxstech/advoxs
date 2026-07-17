from decimal import Decimal
from types import SimpleNamespace

from app.pricing import calcular_creditos

CONFIG = SimpleNamespace(
    tokens_per_credit=1000, input_weight=Decimal("0.3"), output_weight=Decimal("1.0")
)


def test_pondera_input_e_output():
    # 2000*0.3 + 500*1.0 = 1100 tokens ponderados -> 1.1 créditos
    assert calcular_creditos(2000, 500, 2500, CONFIG) == Decimal("1.1000")


def test_arredonda_para_4_casas_half_up():
    # 1*0.3 = 0.3 tokens ponderados -> 0.0003 créditos
    assert calcular_creditos(1, 0, 1, CONFIG) == Decimal("0.0003")
    # 166*0.3 = 49.8 -> 0.0498
    assert calcular_creditos(166, 0, 166, CONFIG) == Decimal("0.0498")


def test_fallback_sem_breakdown_trata_tudo_como_output():
    # agents antigo: breakdown zerado mas tokens_used > 0 -> peso 1.0 (cobra
    # a mais, nunca a menos, só durante a transição de deploy)
    assert calcular_creditos(0, 0, 3500, CONFIG) == Decimal("3.5000")


def test_zero_tokens_zero_creditos():
    assert calcular_creditos(0, 0, 0, CONFIG) == Decimal("0.0000")
