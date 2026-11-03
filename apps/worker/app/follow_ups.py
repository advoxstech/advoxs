"""Renderização do follow-up manual. Duplicado deliberadamente de
apps/api/app/services/follow_ups.py — mesmo padrão de calcular_creditos,
para não acoplar serviços deployados separadamente."""

import re

_VARIABLE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
DEFAULT_CONTACT_NAME = "cliente"
CONTACT_NAME_PLACEHOLDER = "{{nome}}"


def resolve_params(params: list[dict], contact_name: str) -> list[str]:
    values = []
    for param in params:
        if param.get("source") == "contact_name":
            values.append(contact_name.strip() or param.get("value") or DEFAULT_CONTACT_NAME)
        else:
            values.append(param.get("value", ""))
    return values


def render_template(template: dict, values: list[str]) -> str:
    by_name = dict(zip(template["param_names"], values, strict=False))
    body = _VARIABLE.sub(lambda m: by_name.get(m.group(1), m.group(0)), template["body_text"])
    parts = [template.get("header_text"), body, template.get("footer_text")]
    return "\n\n".join(part for part in parts if part)


def render_text(text: str, contact_name: str) -> str:
    return text.replace(CONTACT_NAME_PLACEHOLDER, contact_name.strip() or DEFAULT_CONTACT_NAME)


def template_components(template: dict, values: list[str]) -> list[dict]:
    """Parâmetros do corpo no formato da Cloud API (posicionais ou nomeados)."""
    if not values:
        return []
    named = template.get("parameter_format") == "NAMED"
    parameters = []
    for name, value in zip(template["param_names"], values, strict=False):
        parameter = {"type": "text", "text": value}
        if named:
            parameter["parameter_name"] = name
        parameters.append(parameter)
    return [{"type": "body", "parameters": parameters}]
