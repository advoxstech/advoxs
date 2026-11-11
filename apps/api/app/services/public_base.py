"""Leitura da pasta BASE-PUBLICA (base central + pacotes por especialidade).

A pasta PACOTES tem uma subpasta por especialidade ("01_CIVEL" … "16_AMBIENTAL"),
cada uma com a árvore A–F e Z. Só entram na base da Advoxs os documentos das
pastas que ela preenche: E (modelos), Z (Constituição), Z.1 (legislação
específica) e Z.2 (decisões de tribunais). As demais são do escritório.

O mesmo documento aparece em vários pacotes: a identidade é o conteúdo
(SHA-256), então cada um é lido uma vez só e ligado às especialidades em que
aparece.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from app.core.knowledge_tree import SPECIALTIES

MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}

# "01_CIVEL" → civel: a ordem de SPECIALTIES é a numeração dos pacotes.
_PACKAGE_FOLDER = re.compile(r"^(\d{2})[_ ]")
# Numeração permanente no início do nome do arquivo: 1.01, 2.03a, Z.1.
_CODE = re.compile(r"^(Z\.\d+|\d+\.\d+[a-z]?)[_ ]", re.IGNORECASE)


@dataclass
class PublicFile:
    path: Path
    filename: str
    code: str
    title: str
    category: str
    mime_type: str
    size_bytes: int
    content_sha256: str
    specialties: set[str] = field(default_factory=set)


def _normalize(name: str) -> str:
    text = unicodedata.normalize("NFD", name.upper())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def category_for(parts: tuple[str, ...]) -> str | None:
    """Pasta da árvore a partir do caminho dentro do pacote; None = pasta do escritório."""
    if not parts:
        return None
    folders = [_normalize(part) for part in parts]
    top = folders[0]
    if top.startswith("E)"):
        if len(folders) == 1:
            return "modelos"
        if folders[1].startswith("MODELOS_CONTRATOS"):
            return "modelos_contratos"
        if folders[1].startswith("PECAS_PROCESSUAIS"):
            return "pecas_processuais"
        return None
    if top.startswith("Z)"):
        if len(folders) == 1:
            return "constituicao"
        if folders[1].startswith("1)"):
            return "legislacao_especifica"
        if folders[1].startswith("2)"):
            return "decisoes_tribunais"
    return None


def describe(filename: str) -> tuple[str, str]:
    """(código permanente, título legível) a partir do nome do arquivo."""
    stem = Path(filename).stem
    match = _CODE.match(stem)
    code = match.group(1) if match else ""
    title = stem[match.end() :] if match else stem
    title = re.sub(r"[_ ]texto[_ ]oficial$", "", title, flags=re.IGNORECASE)
    return code, " ".join(title.replace("_", " ").split())


def scan_packages(root: Path) -> list[PublicFile]:
    """Documentos distintos da pasta PACOTES, com as especialidades de cada um."""
    packages = root / "PACOTES"
    if not packages.is_dir():
        raise ValueError(f"Pasta PACOTES não encontrada em {root}")

    by_sha: dict[str, PublicFile] = {}
    filenames: dict[str, str] = {}
    for package in sorted(p for p in packages.iterdir() if p.is_dir()):
        match = _PACKAGE_FOLDER.match(package.name)
        number = int(match.group(1)) if match else 0
        if not 1 <= number <= len(SPECIALTIES):
            raise ValueError(f"Pasta de pacote sem número de especialidade: {package.name}")
        specialty = SPECIALTIES[number - 1]
        for path in sorted(package.rglob("*")):
            if not path.is_file() or path.name.startswith(("_", ".")):
                continue
            mime_type = MIME_TYPES.get(path.suffix.lower())
            category = category_for(path.relative_to(package).parts[:-1])
            if mime_type is None or category is None:
                continue
            data = path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            known = by_sha.get(sha)
            if known is None:
                filename = path.name
                if filenames.setdefault(filename, sha) != sha:
                    # Mesmo nome, conteúdo diferente (ex.: a lista de modelos de cada
                    # especialidade): o nome ganha o pacote para continuar único.
                    filename = f"{path.stem} - {package.name}{path.suffix}"
                    if filenames.setdefault(filename, sha) != sha:
                        raise ValueError(f"Nome de documento repetido: {filename}")
                code, title = describe(path.name)
                known = by_sha[sha] = PublicFile(
                    path=path,
                    filename=filename,
                    code=code,
                    title=title,
                    category=category,
                    mime_type=mime_type,
                    size_bytes=len(data),
                    content_sha256=sha,
                )
            known.specialties.add(specialty)
    return sorted(by_sha.values(), key=lambda item: (item.code, item.filename))
