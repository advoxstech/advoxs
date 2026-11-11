import pytest

from app.services.public_base import category_for, describe, scan_packages


def _write(root, relative: str, content: bytes = b"%PDF conteudo") -> None:
    path = root / "PACOTES" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (("Z) CONSTITUIÇÃO",), "constituicao"),
        (("Z) CONSTITUIÇÃO", "1) LEGISLACAO ESPECIFICA"), "legislacao_especifica"),
        (("Z) CONSTITUIÇÃO", "2) DECISOES_TRIBUNAIS"), "decisoes_tribunais"),
        (("E) Modelos",), "modelos"),
        (("E) Modelos", "MODELOS_CONTRATOS"), "modelos_contratos"),
        (("E) Modelos", "PECAS_PROCESSUAIS"), "pecas_processuais"),
        # Pastas do escritório: nada da base da Advoxs entra nelas.
        (("Z) CONSTITUIÇÃO", "3) Convenção condominial"), None),
        (("C) PROCESSOS_JUDICIAIS",), None),
        (("F) NAO_SELECIONAVEIS",), None),
        ((), None),
    ],
)
def test_pasta_da_arvore_pelo_caminho_dentro_do_pacote(parts, expected) -> None:
    assert category_for(parts) == expected


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        (
            "1.01_Lei_10406-2002_Codigo_Civil_texto_oficial.pdf",
            ("1.01", "Lei 10406-2002 Codigo Civil"),
        ),
        (
            "2.03a_STF_Temas_de_Repercussao_Geral_teses_texto_oficial.pdf",
            ("2.03a", "STF Temas de Repercussao Geral teses"),
        ),
        ("Z.1_Constituicao_Federal_1988_texto_oficial.pdf", ("Z.1", "Constituicao Federal 1988")),
        (
            "LISTA DE MODELOS QUE O ESCRITORIO DEVE TER.docx",
            ("", "LISTA DE MODELOS QUE O ESCRITORIO DEVE TER"),
        ),
    ],
)
def test_codigo_e_titulo_pelo_nome_do_arquivo(filename, expected) -> None:
    assert describe(filename) == expected


def test_documento_repetido_nos_pacotes_e_um_so_com_as_especialidades(tmp_path) -> None:
    lei = "Z) CONSTITUIÇÃO/1) LEGISLACAO ESPECIFICA/1.01_Codigo_Civil_texto_oficial.pdf"
    _write(tmp_path, f"01_CIVEL/{lei}")
    _write(tmp_path, f"15_CONTRATOS/{lei}")
    _write(tmp_path, "15_CONTRATOS/Z) CONSTITUIÇÃO/Z.1_Constituicao_texto_oficial.pdf", b"cf")
    # Ignorados: pasta do escritório, formato não suportado e arquivo de controle.
    _write(tmp_path, "15_CONTRATOS/Z) CONSTITUIÇÃO/3) Contratos assinados/contrato.pdf", b"x")
    _write(tmp_path, "15_CONTRATOS/Z) CONSTITUIÇÃO/planilha.xlsx", b"y")
    _write(tmp_path, "15_CONTRATOS/Z) CONSTITUIÇÃO/_manifesto.pdf", b"z")

    items = scan_packages(tmp_path)

    assert [(i.code, i.category, sorted(i.specialties)) for i in items] == [
        ("1.01", "legislacao_especifica", ["civel", "contratos"]),
        ("Z.1", "constituicao", ["contratos"]),
    ]
    assert items[0].title == "Codigo Civil"
    assert items[0].mime_type == "application/pdf"
    assert items[0].size_bytes == len(b"%PDF conteudo")
    assert len(items[0].content_sha256) == 64


def test_mesmo_nome_com_conteudo_diferente_ganha_o_pacote_no_nome(tmp_path) -> None:
    _write(tmp_path, "01_CIVEL/E) Modelos/LISTA DE MODELOS.docx", b"civel")
    _write(tmp_path, "14_CONDOMINIAL/E) Modelos/LISTA DE MODELOS.docx", b"condominial")

    items = scan_packages(tmp_path)

    assert sorted(i.filename for i in items) == [
        "LISTA DE MODELOS - 14_CONDOMINIAL.docx",
        "LISTA DE MODELOS.docx",
    ]
    assert {i.category for i in items} == {"modelos"}


def test_pasta_sem_pacotes_e_erro(tmp_path) -> None:
    with pytest.raises(ValueError, match="PACOTES"):
        scan_packages(tmp_path)


def test_pasta_de_pacote_sem_numero_valido_e_erro(tmp_path) -> None:
    _write(tmp_path, "99_INEXISTENTE/Z) CONSTITUIÇÃO/Z.1_Constituicao.pdf")

    with pytest.raises(ValueError, match="99_INEXISTENTE"):
        scan_packages(tmp_path)
