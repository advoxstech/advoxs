from decimal import Decimal
from types import SimpleNamespace

from app.pricing import calcular_creditos

CONFIG = SimpleNamespace(
    tokens_per_credit=1000, input_weight=Decimal("0.3"), output_weight=Decimal("1.0")
)


def test_pondera_input_e_output():
    # 2000*0.3 + 500*1.0 = 1100 tokens ponderados -> 1.1 créditos -> arredonda pra 1
    assert calcular_creditos(2000, 500, 2500, CONFIG) == Decimal("1")


def test_arredonda_para_inteiro_half_up():
    # 500*1.0/1000 = 0.5 créditos -> HALF_UP sobe pra 1
    assert calcular_creditos(0, 500, 500, CONFIG) == Decimal("1")
    # 1499*1.0/1000 = 1.499 créditos -> desce pra 1
    assert calcular_creditos(0, 1499, 1499, CONFIG) == Decimal("1")
    # 1500*1.0/1000 = 1.5 créditos -> HALF_UP sobe pra 2
    assert calcular_creditos(0, 1500, 1500, CONFIG) == Decimal("2")


def test_consumo_muito_barato_arredonda_pra_zero():
    # 1*0.3/1000 = 0.0003 créditos -> arredonda pra 0 (sem mínimo de 1 crédito)
    assert calcular_creditos(1, 0, 1, CONFIG) == Decimal("0")


def test_fallback_sem_breakdown_trata_tudo_como_output():
    # agents antigo: breakdown zerado mas tokens_used > 0 -> peso 1.0. 3500/1000
    # = 3.5 créditos -> HALF_UP sobe pra 4 (cobra a mais, só na transição de deploy)
    assert calcular_creditos(0, 0, 3500, CONFIG) == Decimal("4")


def test_zero_tokens_zero_creditos():
    assert calcular_creditos(0, 0, 0, CONFIG) == Decimal("0")
