"""Durable office notifications. SMTP acceptance is not proof of inbox delivery."""

import asyncio
import hashlib
import logging
import smtplib
import uuid
from email.message import EmailMessage

from sqlalchemy import text

from app.config import settings
from app.crypto import decrypt_access_token
from app.safe_logging import safe_error

logger = logging.getLogger(__name__)


def send_email(recipient: str, subject: str, content: str, job_id: str):
    if not settings.gmail_smtp_user or not settings.gmail_smtp_app_password:
        raise RuntimeError("smtp_not_configured")
    message = EmailMessage()
    message["From"] = settings.gmail_smtp_user
    message["To"] = recipient
    message["Subject"] = subject
    message["Message-ID"] = f"<team-{job_id}@advoxs>"
    message.set_content(content)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
        smtp.login(settings.gmail_smtp_user, settings.gmail_smtp_app_password)
        smtp.send_message(message)


async def render_email(session, job):
    base = settings.web_app_url.rstrip("/")
    tenant = (
        (
            await session.execute(
                text("SELECT name,status FROM tenants WHERE id=:id"), {"id": job["tenant_id"]}
            )
        )
        .mappings()
        .first()
    )
    if not tenant or tenant["status"] != "active":
        return None
    if job["kind"] == "invitation":
        invitation = (
            (
                await session.execute(
                    text("""SELECT * FROM team_invitations
          WHERE id=:id AND tenant_id=:tid AND accepted_at IS NULL AND cancelled_at IS NULL
          AND expires_at>now()"""),
                    dict(id=job["invitation_id"], tid=job["tenant_id"]),
                )
            )
            .mappings()
            .first()
        )
        if not invitation or not job["secret_encrypted"]:
            return None
        token = decrypt_access_token(job["secret_encrypted"])
        if hashlib.sha256(token.encode()).hexdigest() != invitation["token_hash"]:
            return None
        return (
            invitation["email"],
            "Advoxs — convite para a equipe",
            (
                f"Você foi convidado para a equipe de {tenant['name']}.\n\n"
                f"Aceite o convite: {base}/convite#token={token}\n\n"
                "Se já tem conta, entre com sua senha atual. Caso contrário, crie uma senha.\n\n"
                "O convite vale por 48 horas. Não há cobrança para aceitar. "
                "Se não reconhecer o convite, ignore esta mensagem."
            ),
        )
    # Recheck current access immediately before sending; links never grant access.
    row = (
        (
            await session.execute(
                text("""SELECT u.email,a.name AS agent_name,c.urgent_since
      FROM conversations c JOIN users u ON u.id=:uid
      JOIN office_memberships membership ON membership.user_id=u.id
        AND membership.tenant_id=c.tenant_id
      LEFT JOIN agents a ON a.id=COALESCE(c.current_agent_id,c.urgent_agent_id,
        (SELECT id FROM agents WHERE tenant_id=c.tenant_id AND is_entry_point LIMIT 1))
      WHERE c.id=:cid AND c.tenant_id=:tid AND c.urgency_episode_id=:episode
        AND c.urgent_since IS NOT NULL AND NOT c.is_test AND c.deletion_requested_at IS NULL
        AND u.active AND membership.active AND (
          EXISTS(SELECT 1 FROM agent_lawyers al WHERE al.tenant_id=c.tenant_id
            AND al.agent_id=a.id AND al.user_id=u.id AND membership.role='lawyer')
          OR (membership.role='admin' AND NOT EXISTS(SELECT 1 FROM agent_lawyers al
            JOIN users v ON v.id=al.user_id
            JOIN office_memberships vm ON vm.user_id=v.id AND vm.tenant_id=al.tenant_id
            WHERE al.tenant_id=c.tenant_id
            AND al.agent_id=a.id AND v.active AND vm.active AND vm.role='lawyer')))
      """),
                dict(
                    uid=job["user_id"],
                    cid=job["conversation_id"],
                    tid=job["tenant_id"],
                    episode=job["episode_id"],
                ),
            )
        )
        .mappings()
        .first()
    )
    if not row:
        return None
    return (
        row["email"],
        "Advoxs — atendimento urgente",
        (
            f"Escritório: {tenant['name']}\nAgente: {row['agent_name'] or 'Atendimento'}\n"
            f"Sinalizado em: {row['urgent_since'].isoformat()}\n\n"
            f"Abrir atendimento: {base}/escritorios?escritorio={job['tenant_id']}"
            f"&conversa={job['conversation_id']}\n\n"
            "A IA continua atendendo até alguém clicar em Assumir atendimento. "
            "Consulte os detalhes no painel."
        ),
    )


async def deliver_team_emails(ctx):
    # A short bounded batch; SKIP LOCKED + lease handles competing workers/crashes.
    factory = ctx["system_session_factory"]
    for _ in range(20):
        claim = uuid.uuid4()
        async with factory() as session:
            job = (
                (
                    await session.execute(
                        text("""UPDATE team_emails SET status='processing',
              locked_at=now(),claim_id=:claim,attempts=attempts+1 WHERE id=(
                SELECT id FROM team_emails WHERE (status='pending' AND available_at<=now())
                OR (status='processing' AND locked_at<now()-interval '5 minutes')
                ORDER BY available_at FOR UPDATE SKIP LOCKED LIMIT 1)
              RETURNING *"""),
                        {"claim": claim},
                    )
                )
                .mappings()
                .first()
            )
            await session.commit()
            if not job:
                return
            job = dict(job)
        error = None
        try:
            async with factory() as session:
                content = await render_email(session, job)
            if content:
                await asyncio.to_thread(send_email, *content, str(job["id"]))
                state = "sent"
            else:
                state = "cancelled"
        except Exception as exc:
            error = type(exc).__name__  # Never log addresses, tokens or provider bodies.
            permanent = isinstance(
                exc, (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused)
            )
            state = "failed" if permanent or job["attempts"] >= 5 else "pending"
            logger.warning("Team email failed | job=%s error_type=%s", job["id"], safe_error(exc))
        async with factory() as session:
            await session.execute(
                text("""UPDATE team_emails SET status=:state,last_error=:error,
              available_at=now()+make_interval(secs => :delay),locked_at=NULL,claim_id=NULL,
              sent_at=CASE WHEN :state='sent' THEN now() ELSE sent_at END,
              secret_encrypted=CASE WHEN :state IN ('sent','cancelled') THEN NULL
                ELSE secret_encrypted END
              WHERE id=:id AND claim_id=:claim"""),
                dict(
                    state=state,
                    error=error,
                    delay=min(60 * 2 ** job["attempts"], 3600),
                    id=job["id"],
                    claim=claim,
                ),
            )
            await session.commit()
