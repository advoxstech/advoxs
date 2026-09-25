"""Provision the shared agents credential before deployment, without logging it."""

import os
import secrets
import stat
import sys
import tempfile
from pathlib import Path

from dotenv import dotenv_values, set_key


def ensure_agents_api_key(path: Path) -> bool:
    # Never create a replacement .env when the deployment path is incorrect.
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("O .env do deploy deve ser um arquivo regular existente.")
    values = dotenv_values(path, interpolate=False)
    current = values.get("AGENTS_API_KEY")
    if current and current.strip():
        return False

    original = path.read_bytes()
    original_stat = path.stat()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".env.agents-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(original)
        # dotenv preserves unrelated lines, including comments and quoted values.
        set_key(temporary, "AGENTS_API_KEY", secrets.token_hex(32), quote_mode="never")
        if hasattr(os, "chown"):
            os.chown(temporary, original_stat.st_uid, original_stat.st_gid)
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        if path.read_bytes() != original:
            raise RuntimeError(
                "O .env mudou durante o preparo; execute o deploy novamente."
            )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


if __name__ == "__main__":
    try:
        created = ensure_agents_api_key(Path(sys.argv[1]))
    except Exception:
        # Parsing/I/O errors must not print any environment contents.
        print(
            "Falha ao preparar a autenticação dos agentes. Deploy interrompido.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        "Chave interna dos agentes preparada."
        if created
        else "Chave interna existente preservada."
    )
