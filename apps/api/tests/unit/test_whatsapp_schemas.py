from app.schemas.whatsapp import extract_inbound_messages, extract_inbound_zapi_message


def _payload(value: dict) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_ID", "changes": [{"field": "messages", "value": value}]}],
    }


def test_extract_text_message() -> None:
    payload = _payload(
        {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "5511999999999", "phone_number_id": "PNID"},
            "contacts": [{"profile": {"name": "Fulano"}, "wa_id": "5511888888888"}],
            "messages": [
                {
                    "from": "5511888888888",
                    "id": "wamid.ABC",
                    "timestamp": "1751900000",
                    "type": "text",
                    "text": {"body": "Olá, preciso de ajuda"},
                }
            ],
        }
    )

    messages = extract_inbound_messages(payload)

    assert len(messages) == 1
    msg = messages[0]
    assert msg.phone_number_id == "PNID"
    assert msg.wa_message_id == "wamid.ABC"
    assert msg.contact_phone_number == "5511888888888"
    assert msg.message_type == "text"
    assert msg.content == "Olá, preciso de ajuda"
    assert msg.media_id is None


def test_extract_media_message_with_caption() -> None:
    payload = _payload(
        {
            "metadata": {"phone_number_id": "PNID"},
            "messages": [
                {
                    "from": "5511888888888",
                    "id": "wamid.MEDIA",
                    "type": "image",
                    "image": {"id": "MEDIA_ID", "mime_type": "image/jpeg", "caption": "segue foto"},
                }
            ],
        }
    )

    messages = extract_inbound_messages(payload)

    assert len(messages) == 1
    msg = messages[0]
    assert msg.content == "segue foto"
    assert msg.media_id == "MEDIA_ID"
    assert msg.media_type == "image/jpeg"


def test_extract_interactive_reply() -> None:
    payload = _payload(
        {
            "metadata": {"phone_number_id": "PNID"},
            "messages": [
                {
                    "from": "5511888888888",
                    "id": "wamid.INT",
                    "type": "interactive",
                    "interactive": {
                        "type": "button_reply",
                        "button_reply": {"id": "btn-1", "title": "Falar com advogado"},
                    },
                }
            ],
        }
    )

    messages = extract_inbound_messages(payload)

    assert len(messages) == 1
    assert messages[0].content == "Falar com advogado"


def test_status_only_payload_is_ignored() -> None:
    payload = _payload(
        {
            "metadata": {"phone_number_id": "PNID"},
            "statuses": [{"id": "wamid.X", "status": "delivered", "recipient_id": "5511..."}],
        }
    )

    assert extract_inbound_messages(payload) == []


def test_non_message_field_is_ignored() -> None:
    payload = {
        "entry": [
            {"changes": [{"field": "account_update", "value": {"event": "VERIFIED_ACCOUNT"}}]}
        ]
    }

    assert extract_inbound_messages(payload) == []


def test_empty_payload() -> None:
    assert extract_inbound_messages({}) == []


def _zapi_payload(**overrides: object) -> dict:
    base = {
        "instanceId": "inst-123",
        "phone": "5511888888888",
        "messageId": "msg-abc",
        "fromMe": False,
        "text": {"message": "Olá, preciso de ajuda"},
    }
    base.update(overrides)
    return base


def test_extract_zapi_text_message() -> None:
    result = extract_inbound_zapi_message(_zapi_payload())

    assert result is not None
    assert result.zapi_instance_id == "inst-123"
    assert result.wa_message_id == "msg-abc"
    assert result.contact_phone_number == "5511888888888"
    assert result.content == "Olá, preciso de ajuda"


def test_extract_zapi_ignora_mensagem_from_me() -> None:
    result = extract_inbound_zapi_message(_zapi_payload(fromMe=True))

    assert result is None


def test_extract_zapi_ignora_sem_texto() -> None:
    result = extract_inbound_zapi_message(_zapi_payload(text=None))

    assert result is None


def test_extract_zapi_ignora_payload_sem_instance_id() -> None:
    payload = _zapi_payload()
    del payload["instanceId"]

    result = extract_inbound_zapi_message(payload)

    assert result is None
