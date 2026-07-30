"""Envio de mensagem de texto avulsa pro contato de uma conversa, roteado
pelo provedor de WhatsApp conectado do tenant (Meta ou Z-API) — ponto único
reaproveitado por todo lugar do `api` que manda uma mensagem fora do fluxo do
agents: resposta manual do takeover humano (`app/api/v1/conversations.py`),
aviso de isenção de cobrança do cliente final (idem) e confirmação de compra/
cancelamento de assinatura do cliente final
(`app/services/end_customer_billing.py`).

Antes desta função, cada um desses 3 pontos chamava
`app.clients.whatsapp.send_text_message` incondicionalmente — quebrava com
`AttributeError` (`None.encode()`) pra um tenant conectado via Z-API, já que
`phone_number_id`/`access_token_encrypted` são sempre NULL nesse caso (CHECK
constraint `ck_whatsapp_numbers_provider_fields`).

Reempacota `ZApiNetworkError`/`ZApiApiError` como `WhatsAppSendError` — os 3
call sites já sabem capturar esse único tipo de exceção, evitando reescrever
o tratamento de erro de cada um.
"""

from app.clients.whatsapp import WhatsAppSendError, send_text_message
from app.clients.zapi import ZApiApiError, ZApiNetworkError, send_zapi_text_message
from app.core.crypto import decrypt_access_token
from app.models import WhatsAppNumber


async def send_text_to_contact(number: WhatsAppNumber, to: str, text: str) -> None:
    """Envia `text` pro contato `to`, usando as credenciais de `number` —
    ramifica por `number.provider`. Levanta `WhatsAppSendError` em qualquer
    falha, seja da Graph API (Meta) ou da Z-API."""
    if number.provider == "zapi":
        token = decrypt_access_token(number.zapi_instance_token_encrypted)
        client_token = (
            decrypt_access_token(number.zapi_client_token_encrypted)
            if number.zapi_client_token_encrypted
            else None
        )
        try:
            await send_zapi_text_message(
                instance_id=number.zapi_instance_id,
                token=token,
                client_token=client_token,
                to=to,
                text=text,
            )
        except (ZApiNetworkError, ZApiApiError) as exc:
            raise WhatsAppSendError(str(exc)) from exc
    else:
        await send_text_message(
            phone_number_id=number.phone_number_id,
            access_token=decrypt_access_token(number.access_token_encrypted),
            to=to,
            text=text,
        )
