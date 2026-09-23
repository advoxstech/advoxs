"""Falha quando código de produção envia valores sensíveis diretamente ao logger."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    ROOT / "apps/api/app",
    ROOT / "apps/worker/app",
    ROOT / "apps/agents",
    ROOT / "apps/api_rag",
)
LOGGER_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
SENSITIVE_NAMES = {
    "access_token",
    "args",
    "body",
    "contact_phone_number",
    "content",
    "e",
    "error",
    "exc",
    "filename",
    "headers",
    "link",
    "log_url",
    "message",
    "password",
    "payload",
    "phone_number_id",
    "query",
    "request",
    "response",
    "secret",
    "text",
    "thread_id",
    "token",
    "url",
}
SAFE_WRAPPERS = {"len", "safe_error", "safe_identifier", "safe_url", "type"}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_sensitive(node: ast.AST) -> bool:
    if isinstance(node, ast.Call) and _call_name(node) in SAFE_WRAPPERS:
        return False
    if isinstance(node, ast.Name):
        return node.id in SENSITIVE_NAMES
    if isinstance(node, ast.Attribute):
        if node.attr == "text" and isinstance(node.value, ast.Name):
            return node.value.id in {"response", "request"}
        return node.attr in SENSITIVE_NAMES
    if isinstance(node, ast.FormattedValue):
        return _is_sensitive(node.value)
    if isinstance(node, ast.JoinedStr):
        return any(_is_sensitive(value) for value in node.values)
    if isinstance(node, ast.BinOp):
        return _is_sensitive(node.left) or _is_sensitive(node.right)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(_is_sensitive(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return any(_is_sensitive(item) for item in [*node.keys, *node.values] if item)
    return False


def _production_files() -> list[Path]:
    files: list[Path] = []
    for source_root in SOURCE_ROOTS:
        for path in source_root.rglob("*.py"):
            relative_parts = path.relative_to(source_root).parts
            if "tests" in relative_parts or path.name.startswith("test_"):
                continue
            files.append(path)
    return files


def find_violations() -> list[str]:
    violations: list[str] = []
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                is_logger_call = (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "logger"
                    and node.func.attr in LOGGER_METHODS
                )
                if is_logger_call and any(
                    _is_sensitive(argument)
                    for argument in [
                        *node.args,
                        *(keyword.value for keyword in node.keywords),
                    ]
                ):
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: log sensível"
                    )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
                and any(
                    isinstance(argument, ast.Name) and argument.id == "DATABASE_URL"
                    for argument in node.args
                )
            ):
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: DATABASE_URL em print"
                )
    return violations


if __name__ == "__main__":
    found = find_violations()
    if found:
        raise SystemExit("Logs potencialmente sensíveis:\n" + "\n".join(found))
    print("Política de logs sensíveis verificada.")
