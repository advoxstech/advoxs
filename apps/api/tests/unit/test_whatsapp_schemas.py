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


def test_extract_media_message_com_caption_null_nao_quebra() -> None:
    # Mesmo padrão de bug real corrigido do lado da Z-API — defensivo aqui
    # também, caso a Meta em algum payload mande "caption": null em vez de
    # omitir a chave.
    payload = _payload(
        {
            "metadata": {"phone_number_id": "PNID"},
            "messages": [
                {
                    "from": "5511888888888",
                    "id": "wamid.MEDIA2",
                    "type": "document",
                    "document": {
                        "id": "MEDIA_ID",
                        "mime_type": "application/pdf",
                        "caption": None,
                    },
                }
            ],
        }
    )

    messages = extract_inbound_messages(payload)

    assert len(messages) == 1
    assert messages[0].content == ""
    assert messages[0].media_id == "MEDIA_ID"


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


def test_extract_zapi_imagem_com_legenda() -> None:
    payload = _zapi_payload(
        text=None,
        image={
            "mimeType": "image/jpeg",
            "imageUrl": "https://z-api.example/media/foto.jpg",
            "caption": "Segue o documento",
        },
    )

    result = extract_inbound_zapi_message(payload)

    assert result is not None
    assert result.content == "Segue o documento"
    assert result.media_url == "https://z-api.example/media/foto.jpg"
    assert result.media_type == "image/jpeg"


def test_extract_zapi_audio_sem_legenda_ainda_persiste() -> None:
    """Áudio (e documento) não têm campo "caption" — mensagem sem texto
    nenhum, mas com mídia, não deve ser descartada."""
    payload = _zapi_payload(
        text=None,
        audio={
            "ptt": True,
            "audioUrl": "https://z-api.example/media/audio.ogg",
            "mimeType": "audio/ogg; codecs=opus",
        },
    )

    result = extract_inbound_zapi_message(payload)

    assert result is not None
    assert result.content == ""
    assert result.media_url == "https://z-api.example/media/audio.ogg"
    assert result.media_type == "audio/ogg; codecs=opus"


def test_extract_zapi_documento_sem_legenda_com_caption_null_nao_quebra() -> None:
    # Regressão real (confirmado em produção): a Z-API manda "caption": null
    # (chave presente, valor None) quando o documento não tem legenda — não
    # omite a chave. media.get("caption", "") só cai no default quando a
    # chave NÃO existe, então isso virava content=None e quebrava a
    # validação do Pydantic (content exige str) com 500, descartando a
    # mensagem inteira antes de persistir.
    payload = _zapi_payload(
        text=None,
        document={
            "documentUrl": "https://z-api.example/media/curriculo.pdf",
            "mimeType": "application/pdf",
            "fileName": "curriculo.pdf",
            "caption": None,
        },
    )

    result = extract_inbound_zapi_message(payload)

    assert result is not None
    assert result.content == ""
    assert result.media_url == "https://z-api.example/media/curriculo.pdf"


def test_extract_zapi_documento_usa_mime_type() -> None:
    payload = _zapi_payload(
        text=None,
        document={
            "documentUrl": "https://z-api.example/media/contrato.pdf",
            "mimeType": "application/pdf",
            "fileName": "contrato.pdf",
        },
    )

    result = extract_inbound_zapi_message(payload)

    assert result is not None
    assert result.media_url == "https://z-api.example/media/contrato.pdf"
    assert result.media_type == "application/pdf"


def test_extract_zapi_video_com_legenda() -> None:
    payload = _zapi_payload(
        text=None,
        video={
            "videoUrl": "https://z-api.example/media/video.mp4",
            "caption": "Prova em vídeo",
            "mimeType": "video/mp4",
        },
    )

    result = extract_inbound_zapi_message(payload)

    assert result is not None
    assert result.content == "Prova em vídeo"
    assert result.media_url == "https://z-api.example/media/video.mp4"
    assert result.media_type == "video/mp4"


def test_extract_zapi_texto_simples_nao_tem_media_url() -> None:
    result = extract_inbound_zapi_message(_zapi_payload())

    assert result is not None
    assert result.media_url is None
    assert result.media_type is None


def test_extract_zapi_resposta_de_lista_usa_o_title() -> None:
    payload = _zapi_payload()
    del payload["text"]
    payload["listResponseMessage"] = {"title": "Básico", "selectedRowId": "Básico"}

    result = extract_inbound_zapi_message(payload)

    assert result is not None
    assert result.content == "Básico"


def test_extract_zapi_lista_tem_prioridade_sobre_texto() -> None:
    payload = _zapi_payload()
    payload["listResponseMessage"] = {"title": "Premium", "selectedRowId": "Premium"}

    result = extract_inbound_zapi_message(payload)

    assert result is not None
    assert result.content == "Premium"


def test_extract_zapi_lista_sem_title_ignora_e_cai_no_texto() -> None:
    payload = _zapi_payload()
    payload["listResponseMessage"] = {"selectedRowId": "Básico"}

    result = extract_inbound_zapi_message(payload)

    assert result is not None
    assert result.content == "Olá, preciso de ajuda"
