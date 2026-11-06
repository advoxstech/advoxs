"""Envio de mensagem via WhatsApp Cloud API (Graph API) direto do worker —
usado só pelo billing gate determinístico (apps/worker/app/billing_gate.py),
que precisa mandar texto e listas interativas SEM passar pelo agents
service (é esse desvio que elimina o custo de LLM nesse trecho do funil).
Duplicado deliberadamente de apps/api/app/clients/whatsapp.py — mesmo padrão
já usado no projeto pra evitar acoplamento entre serviços deployados
separadamente (ex: calcular_creditos)."""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class WhatsAppSendError(Exception):
    pass


def _url(phone_number_id: str) -> str:
    return f"{settings.graph_api_base_url}/{settings.graph_api_version}/{phone_number_id}/messages"


async def _post(phone_number_id: str, access_token: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                _url(phone_number_id),
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise WhatsAppSendError("Falha de rede ao chamar a Graph API") from exc

    if response.is_error:
        logger.warning("Graph API retornou erro | status=%s", response.status_code)
        raise WhatsAppSendError(f"Graph API HTTP {response.status_code}")


async def send_text_message(phone_number_id: str, access_token: str, to: str, text: str) -> None:
    await _post(
        phone_number_id,
        access_token,
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text},
        },
    )


async def send_document_message(
    phone_number_id: str,
    access_token: str,
    to: str,
    link: str,
    filename: str | None = None,
) -> None:
    document: dict[str, str] = {"link": link}
    if filename:
        document["filename"] = filename
    await _post(
        phone_number_id,
        access_token,
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "document",
            "document": document,
        },
    )


async def send_interactive_list_message(
    phone_number_id: str,
    access_token: str,
    to: str,
    header: str,
    body: str,
    sections: list[dict],
    button_text: str = "Ver opções",
) -> None:
    """`sections`: `[{"title": str, "rows": [{"id": str, "title": str, "description": str}]}]`.
    Limite da Meta: até 10 seções, no máximo 10 linhas somadas entre todas."""
    await _post(
        phone_number_id,
        access_token,
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": header},
                "body": {"text": body},
                "action": {"button": button_text, "sections": sections},
            },
        },
    )


class WhatsAppTemplateError(Exception):
    """Falha ao enviar modelo; `retryable` separa instabilidade de recusa definitiva."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


# Códigos da Cloud API com mensagem própria no painel do escritório.
_TEMPLATE_ERRORS = {
    130429: "Limite de envio da Meta atingido.",
    131026: "O número não pode receber mensagens (sem WhatsApp ou fora dos termos).",
    131049: "A Meta limitou mensagens de marketing para este contato.",
    131050: "O contato optou por não receber mensagens de marketing.",
    131056: "Muitas mensagens para este contato em pouco tempo.",
    132001: "O modelo não existe mais na Meta.",
    132015: "O modelo foi pausado pela Meta.",
    132016: "O modelo foi desativado pela Meta.",
}
_RETRYABLE_CODES = {130429, 131056}


async def send_template_message(
    phone_number_id: str,
    access_token: str,
    to: str,
    name: str,
    language: str,
    components: list[dict],
) -> None:
    template: dict = {"name": name, "language": {"code": language}}
    if components:
        template["components"] = components
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                _url(phone_number_id),
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": to,
                    "type": "template",
                    "template": template,
                },
            )
    except httpx.HTTPError as exc:
        raise WhatsAppTemplateError("Falha de rede ao chamar a Meta.", retryable=True) from exc

    if not response.is_error:
        return
    try:
        error = response.json().get("error") or {}
    except ValueError:
        error = {}
    code = error.get("code")
    logger.warning(
        "Graph API (template) retornou erro | status=%s code=%s", response.status_code, code
    )
    message = (
        _TEMPLATE_ERRORS.get(code)
        or error.get("message")
        or (f"A Meta recusou o envio (HTTP {response.status_code}).")
    )
    retryable = (
        response.status_code >= 500 or response.status_code == 429 or code in _RETRYABLE_CODES
    )
    raise WhatsAppTemplateError(str(message)[:500], retryable=retryable)
