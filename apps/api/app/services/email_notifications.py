"""Notificação por e-mail (Gmail SMTP) — hoje só usada pra avisar a Advoxs
quando um tenant pede a conexão Z-API gerenciada (ver app/services/
zapi_provisioning_requests.py e POST /whatsapp/request-managed-zapi).

Best-effort, igual a toda integração externa deste código-base: uma falha
de envio (Gmail fora do ar, credencial errada) nunca deve impedir o pedido
do tenant de ser registrado — só loga um aviso. Sem as 3 credenciais
configuradas (GMAIL_SMTP_USER, GMAIL_SMTP_APP_PASSWORD,
ADMIN_NOTIFICATION_EMAIL), o envio é pulado em silêncio, sem erro — mesmo
padrão de "falha aberta" já usado por outras integrações opcionais deste
projeto (ex: AGENTS_API_KEY).

smtplib é bloqueante (não tem cliente async na stdlib) — por isso o envio
de verdade roda em `asyncio.to_thread`, pra não travar o event loop
enquanto espera a resposta do Gmail.
"""

import asyncio
import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)

_GMAIL_SMTP_HOST = "smtp.gmail.com"
_GMAIL_SMTP_PORT = 465


def _send_email_sync(to_address: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.gmail_smtp_user
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP_SSL(_GMAIL_SMTP_HOST, _GMAIL_SMTP_PORT) as server:
        server.login(settings.gmail_smtp_user, settings.gmail_smtp_app_password)
        server.send_message(message)


_GMAIL_NOT_CONFIGURED = "Notificação de pedido Z-API pulada — Gmail SMTP não configurado"


async def send_zapi_request_notification(tenant_name: str, requested_at: datetime) -> None:
    gmail_configured = bool(
        settings.gmail_smtp_user
        and settings.gmail_smtp_app_password
        and settings.admin_notification_email
    )
    if not gmail_configured:
        logger.debug("%s | tenant=%s", _GMAIL_NOT_CONFIGURED, tenant_name)
        return

    subject = f"Advoxs — {tenant_name} pediu conexão Z-API gerenciada"
    when = requested_at.strftime("%d/%m/%Y %H:%M")
    body = (
        f"O escritório {tenant_name} pediu, em {when}, que a Advoxs configure a conexão "
        "de WhatsApp via Z-API por ele.\n\n"
        "Provisione a instância no painel de administração (/admin/tenants) quando puder."
    )

    try:
        await asyncio.to_thread(_send_email_sync, settings.admin_notification_email, subject, body)
    except Exception as exc:  # noqa: BLE001 — best-effort, nunca deve propagar
        logger.warning(
            "Falha ao enviar notificação de pedido Z-API por e-mail (best-effort) | erro=%s", exc
        )
