"""Notificação por e-mail (Gmail SMTP) — avisa a Advoxs quando o saldo de
créditos de um tenant zera (ver _debitar_creditos em app/tasks/messages.py).

Mesmo mecanismo já usado em apps/api/app/services/email_notifications.py,
duplicado aqui porque o worker é um serviço Python separado (mesma
convenção já usada neste projeto pra outras constantes compartilhadas,
ex: DOCUMENT_GENERATION_CREDIT_COST em app/pricing.py). Best-effort: uma
falha de envio nunca deve propagar — só loga um aviso.
"""

import asyncio
import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage

from app.config import settings

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


async def send_tenant_out_of_credits_notification(tenant_name: str, ran_out_at: datetime) -> None:
    gmail_configured = bool(
        settings.gmail_smtp_user
        and settings.gmail_smtp_app_password
        and settings.admin_notification_email
    )
    if not gmail_configured:
        logger.debug(
            "Notificação de saldo esgotado pulada — Gmail SMTP não configurado | tenant=%s",
            tenant_name,
        )
        return

    subject = f"Advoxs — créditos de {tenant_name} zeraram"
    when = ran_out_at.strftime("%d/%m/%Y %H:%M")
    body = (
        f"O escritório {tenant_name} ficou sem créditos em {when} — o agente "
        "parou de responder os clientes desse escritório em silêncio, até ele recarregar.\n\n"
        "Pode valer a pena avisar o escritório sobre a recarga."
    )

    try:
        await asyncio.to_thread(_send_email_sync, settings.admin_notification_email, subject, body)
    except Exception as exc:  # noqa: BLE001 — best-effort, nunca deve propagar
        logger.warning(
            "Falha ao enviar notificação de saldo esgotado por e-mail (best-effort) | erro=%s", exc
        )
