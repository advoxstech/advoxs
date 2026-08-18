"""Notificação por e-mail (Gmail SMTP) pra avisar a Advoxs de eventos que
merecem atenção: pedido/cancelamento de conexão Z-API gerenciada (ver
app/services/zapi_provisioning_requests.py), desconexão de WhatsApp e novo
escritório cadastrado.

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


_GMAIL_NOT_CONFIGURED = "Notificação pulada — Gmail SMTP não configurado"


def _gmail_configured() -> bool:
    return bool(
        settings.gmail_smtp_user
        and settings.gmail_smtp_app_password
        and settings.admin_notification_email
    )


async def _dispatch(subject: str, body: str, *, context: str) -> None:
    if not _gmail_configured():
        logger.debug("%s | %s", _GMAIL_NOT_CONFIGURED, context)
        return

    try:
        await asyncio.to_thread(_send_email_sync, settings.admin_notification_email, subject, body)
    except Exception as exc:  # noqa: BLE001 — best-effort, nunca deve propagar
        logger.warning("Falha ao enviar notificação por e-mail (best-effort) | erro=%s", exc)


async def send_zapi_request_notification(tenant_name: str, requested_at: datetime) -> None:
    subject = f"Advoxs — {tenant_name} pediu conexão Z-API gerenciada"
    when = requested_at.strftime("%d/%m/%Y %H:%M")
    body = (
        f"O escritório {tenant_name} pediu, em {when}, que a Advoxs configure a conexão "
        "de WhatsApp via Z-API por ele.\n\n"
        "Provisione a instância no painel de administração (/admin/tenants) quando puder."
    )
    await _dispatch(subject, body, context=f"tenant={tenant_name}")


async def send_zapi_request_cancelled_notification(
    tenant_name: str, cancelled_at: datetime
) -> None:
    subject = f"Advoxs — {tenant_name} cancelou o pedido de conexão Z-API gerenciada"
    when = cancelled_at.strftime("%d/%m/%Y %H:%M")
    body = (
        f"O escritório {tenant_name} cancelou, em {when}, o pedido de conexão Z-API "
        "gerenciada pela Advoxs.\n\n"
        "Se você já tinha começado a provisionar a instância manualmente, não precisa "
        "mais continuar."
    )
    await _dispatch(subject, body, context=f"tenant={tenant_name}")


async def send_whatsapp_disconnected_notification(
    tenant_name: str, provider: str, disconnected_at: datetime
) -> None:
    provider_label = "Z-API" if provider == "zapi" else "WhatsApp Business oficial"
    subject = f"Advoxs — WhatsApp de {tenant_name} foi desconectado"
    when = disconnected_at.strftime("%d/%m/%Y %H:%M")
    body = (
        f"O WhatsApp do escritório {tenant_name} (via {provider_label}) foi desconectado "
        f"em {when}.\n\n"
        "Enquanto ficar assim, o agente não responde os clientes desse escritório — "
        "pode valer a pena avisar o escritório ou ajudar a reconectar."
    )
    await _dispatch(subject, body, context=f"tenant={tenant_name}")


async def send_new_tenant_notification(
    tenant_name: str, package_name: str, signed_up_at: datetime
) -> None:
    subject = f"Advoxs — novo escritório cadastrado: {tenant_name}"
    when = signed_up_at.strftime("%d/%m/%Y %H:%M")
    body = f"O escritório {tenant_name} se cadastrou e pagou o pacote {package_name} em {when}."
    await _dispatch(subject, body, context=f"tenant={tenant_name}")
