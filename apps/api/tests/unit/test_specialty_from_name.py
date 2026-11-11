import importlib.util
from pathlib import Path

import pytest

from app.core.knowledge_tree import SPECIALTIES, specialty_from_name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Cível", "civel"),
        ("Direito Civil", "civel"),
        ("Trabalhista", "trabalhista"),
        ("Direito do Trabalho", "trabalhista"),
        ("Tributário", "tributario"),
        ("DIREITO TRIBUTÁRIO", "tributario"),
        ("Empresarial", "empresarial"),
        ("Direito do Consumidor", "consumidor"),
        ("Família e Sucessões", "familia_sucessoes"),
        ("Bancário e Financeiro", "bancario_financeiro"),
        ("Digital e Proteção de Dados", "digital_protecao_dados"),
        ("Criminal", "penal"),
        ("  condominial  ", "condominial"),
        ("Contratos", "contratos"),
    ],
)
def test_nome_da_area_vira_especialidade(name, expected) -> None:
    assert specialty_from_name(name) == expected


@pytest.mark.parametrize(
    "name", ["Secretária", "Recepção", "Contratos Imobiliários", "Dr. Augusto", "", "Direito"]
)
def test_nome_sem_area_exata_fica_sem_especialidade(name) -> None:
    assert specialty_from_name(name) is None


def test_toda_especialidade_tem_nome_reconhecido() -> None:
    from app.core.knowledge_tree import _SPECIALTY_NAMES

    assert set(_SPECIALTY_NAMES) == set(SPECIALTIES)


def test_migration_0054_usa_a_mesma_regra_do_app() -> None:
    path = Path(__file__).parents[2] / "alembic/versions/0054_especialidade_pelo_nome.py"
    spec = importlib.util.spec_from_file_location("m0054", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in [
        "Cível",
        "Direito do Trabalho",
        "Tributário",
        "Secretária",
        "Contratos Imobiliários",
    ]:
        assert module._BY_NAME.get(module._key(name)) == specialty_from_name(name)
