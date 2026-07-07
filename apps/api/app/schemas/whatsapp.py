"""Parsing do payload de webhook da WhatsApp Cloud API (Meta).

Formato de referência:
https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples
"""

from pydantic import BaseModel

# Tipos de mensagem com corpo de mídia (o campo do payload tem o mesmo nome do tipo).
MEDIA_TYPES = {"image", "audio", "video", "document", "sticker"}


class InboundWhatsAppMessage(BaseModel):
    """Uma mensagem de contato extraída do webhook, já normalizada."""

    phone_number_id: str
    wa_message_id: str
    contact_phone_number: str
    message_type: str
    content: str = ""
    # ID de mídia da Meta (download exige o access token do tenant — feito depois,
    # não no webhook). Guardado para processamento futuro.
    media_id: str | None = None
    media_type: str | None = None


def extract_inbound_messages(payload: dict) -> list[InboundWhatsAppMessage]:
    """Extrai as mensagens de contato de um payload de webhook da Meta.

    Ignora eventos que não são mensagem (statuses de entrega/leitura, updates
    de template etc.) e entradas malformadas — o webhook precisa responder 200
    rápido mesmo para eventos que não interessam.
    """
    inbound: list[InboundWhatsAppMessage] = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id")
            if not phone_number_id:
                continue

            for message in value.get("messages", []):
                wa_message_id = message.get("id")
                sender = message.get("from")
                message_type = message.get("type", "")
                if not wa_message_id or not sender:
                    continue

                content = ""
                media_id = None
                media_type = None
                if message_type == "text":
                    content = message.get("text", {}).get("body", "")
                elif message_type in MEDIA_TYPES:
                    body = message.get(message_type, {})
                    content = body.get("caption", "")
                    media_id = body.get("id")
                    media_type = body.get("mime_type") or message_type
                elif message_type == "interactive":
                    interactive = message.get("interactive", {})
                    reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
                    content = reply.get("title", "")
                elif message_type == "button":
                    content = message.get("button", {}).get("text", "")
                # Outros tipos (location, contacts, reaction...): persiste sem
                # conteúdo textual, só o tipo — evita perder o evento.

                inbound.append(
                    InboundWhatsAppMessage(
                        phone_number_id=phone_number_id,
                        wa_message_id=wa_message_id,
                        contact_phone_number=sender,
                        message_type=message_type,
                        content=content,
                        media_id=media_id,
                        media_type=media_type,
                    )
                )

    return inbound
