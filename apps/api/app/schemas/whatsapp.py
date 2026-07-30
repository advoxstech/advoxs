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


class InboundZApiMessage(BaseModel):
    """Uma mensagem de contato extraída do webhook da Z-API, já normalizada."""

    zapi_instance_id: str
    wa_message_id: str
    contact_phone_number: str
    content: str = ""
    # Diferente do media_id da Meta (opaco, exige um download autenticado
    # separado), a Z-API já entrega a URL de mídia pronta pra uso no próprio
    # payload do webhook — não precisa de uma chamada extra.
    media_url: str | None = None
    media_type: str | None = None


# Nome do campo do objeto de mídia -> chave que contém a URL, por tipo —
# confirmado na doc oficial (developer.z-api.io/webhooks/on-message-received).
_ZAPI_MEDIA_URL_FIELDS = {
    "image": "imageUrl",
    "audio": "audioUrl",
    "document": "documentUrl",
    "video": "videoUrl",
}


def extract_inbound_zapi_message(payload: dict) -> InboundZApiMessage | None:
    """Extrai a mensagem de um payload de webhook da Z-API — diferente da
    Meta, cada POST já é 1 mensagem só, sem lote. Ignora eco de mensagem
    enviada pelo próprio WhatsApp Web conectado (fromMe=true).

    Reconhece três formatos de conteúdo, nesta ordem de prioridade: resposta
    de uma lista interativa enviada pelo billing gate determinístico
    (`listResponseMessage.title`, quando o cliente final escolhe um pacote de
    créditos — ver apps/worker/app/billing_gate.py; checado primeiro porque
    uma resposta de lista não vem acompanhada de um campo `text` populado),
    texto simples (`text.message`), e mídia (`image`/`audio`/`document`/
    `video`, cada um com sua própria chave de URL — `imageUrl`, `audioUrl`
    etc. — e um `mimeType` comum; `caption` só existe em image/video, por
    isso o `.get` com default). Uma mensagem de mídia sem legenda (áudio,
    documento) ainda é persistida — só o texto vem vazio."""
    if payload.get("fromMe"):
        return None

    instance_id = payload.get("instanceId")
    message_id = payload.get("messageId")
    sender = payload.get("phone")

    content = ""
    media_url = None
    media_type = None

    list_response = payload.get("listResponseMessage")
    if isinstance(list_response, dict) and list_response.get("title"):
        content = list_response["title"]
    else:
        text = payload.get("text") or {}
        if isinstance(text, dict) and text.get("message"):
            content = text["message"]
        else:
            for kind, url_field in _ZAPI_MEDIA_URL_FIELDS.items():
                media = payload.get(kind)
                if isinstance(media, dict) and media.get(url_field):
                    media_url = media[url_field]
                    media_type = media.get("mimeType") or kind
                    content = media.get("caption", "")
                    break

    if not instance_id or not message_id or not sender:
        return None
    if not content and not media_url:
        return None

    return InboundZApiMessage(
        zapi_instance_id=instance_id,
        wa_message_id=message_id,
        contact_phone_number=sender,
        content=content,
        media_url=media_url,
        media_type=media_type,
    )
