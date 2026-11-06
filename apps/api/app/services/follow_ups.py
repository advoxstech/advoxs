"""Regras do follow-up manual: quais modelos da Meta o Advoxs consegue enviar e
como os parâmetros viram texto. A renderização é espelhada em
apps/worker/app/follow_ups.py (mesmo padrão de duplicação entre serviços)."""

import re

_VARIABLE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_SUPPORTED_BUTTONS = {"QUICK_REPLY", "PHONE_NUMBER", "URL"}
DEFAULT_CONTACT_NAME = "cliente"
CONTACT_NAME_PLACEHOLDER = "{{nome}}"


def variable_names(text: str) -> list[str]:
    names: list[str] = []
    for match in _VARIABLE.finditer(text or ""):
        if match.group(1) not in names:
            names.append(match.group(1))
    return names


def _unsupported_reason(raw: dict, header: dict | None, body: dict | None) -> str | None:
    if raw.get("category") == "AUTHENTICATION":
        return "Modelos de autenticação não são usados em follow-up."
    if body is None:
        return "Modelo sem corpo de texto."
    if header is not None:
        if header.get("format", "TEXT") != "TEXT":
            return "Cabeçalho com mídia ainda não é suportado."
        if variable_names(header.get("text", "")):
            return "Cabeçalho com variável ainda não é suportado."
    for component in raw.get("components") or []:
        kind = component.get("type")
        if kind in {"HEADER", "BODY", "FOOTER"}:
            continue
        if kind != "BUTTONS":
            return "Este formato de modelo ainda não é suportado."
        for button in component.get("buttons") or []:
            if button.get("type") not in _SUPPORTED_BUTTONS:
                return "Botões deste tipo ainda não são suportados."
            if variable_names(button.get("url", "")):
                return "Botão com link variável ainda não é suportado."
    return None


def parse_template(raw: dict) -> dict:
    """Resume um modelo da Graph API no formato usado pelo painel e pelo worker."""
    components = {c.get("type"): c for c in raw.get("components") or []}
    header, body = components.get("HEADER"), components.get("BODY")
    reason = _unsupported_reason(raw, header, body)
    body_text = (body or {}).get("text", "")
    return {
        "name": raw.get("name", ""),
        "language": raw.get("language", ""),
        "status": raw.get("status", ""),
        "category": raw.get("category", ""),
        "parameter_format": (raw.get("parameter_format") or "POSITIONAL").upper(),
        "header_text": (header or {}).get("text") if header else None,
        "body_text": body_text,
        "footer_text": components.get("FOOTER", {}).get("text"),
        "param_names": variable_names(body_text),
        "supported": reason is None,
        "unsupported_reason": reason,
        "rejected_reason": raw.get("rejected_reason") or None,
    }


def resolve_params(params: list[dict], contact_name: str) -> list[str]:
    values = []
    for param in params:
        if param.get("source") == "contact_name":
            values.append(contact_name.strip() or param.get("value") or DEFAULT_CONTACT_NAME)
        else:
            values.append(param.get("value", ""))
    return values


def render_template(template: dict, values: list[str]) -> str:
    """Texto que o contato recebe, gravado no histórico da conversa."""
    by_name = dict(zip(template["param_names"], values, strict=False))
    body = _VARIABLE.sub(lambda m: by_name.get(m.group(1), m.group(0)), template["body_text"])
    parts = [template.get("header_text"), body, template.get("footer_text")]
    return "\n\n".join(part for part in parts if part)


def render_text(text: str, contact_name: str) -> str:
    return text.replace(CONTACT_NAME_PLACEHOLDER, contact_name.strip() or DEFAULT_CONTACT_NAME)
