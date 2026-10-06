"""Confirmação de e-mail que antecede o checkout do cadastro."""

import asyncio
import hashlib
import logging
import secrets
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.models import CreditPackage, PendingSignup, User
from app.schemas.signup import SignupCheckoutRequest

logger = logging.getLogger(__name__)

VERIFY_TTL = timedelta(minutes=30)
CHECKOUT_TTL = timedelta(hours=24)
RESEND_DELAY = timedelta(seconds=60)
SEND_WINDOW = timedelta(hours=1)
MAX_SENDS = 3


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _send_verification_sync(email: str, token: str) -> None:
    message = EmailMessage()
    message["From"] = settings.gmail_smtp_user
    message["To"] = email
    message["Subject"] = "Advoxs — confirme seu e-mail para continuar o cadastro"
    link = f"{settings.web_app_url.rstrip('/')}/cadastro/verificar?token={token}"
    message.set_content(
        "Confirme seu e-mail para continuar o cadastro do escritório no Advoxs.\n\n"
        f"{link}\n\nO link vale por 30 minutos. Nenhuma cobrança foi feita. "
        "Se você não pediu este cadastro, ignore esta mensagem."
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
        server.login(settings.gmail_smtp_user, settings.gmail_smtp_app_password)
        server.send_message(message)


async def request_verification(session: AsyncSession, body: SignupCheckoutRequest) -> None:
    if not settings.gmail_smtp_user or not settings.gmail_smtp_app_password:
        raise HTTPException(
            status_code=503, detail="Confirmação por e-mail indisponível no momento."
        )

    email = str(body.email).lower()
    if await session.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=409, detail="Este e-mail já está cadastrado — faça login.")
    package = await session.get(CreditPackage, body.credit_package_id)
    if package is None or not package.active:
        raise HTTPException(status_code=400, detail="Pacote de créditos inválido.")

    pending = await session.scalar(
        select(PendingSignup).where(PendingSignup.email == email).with_for_update()
    )
    if pending is None:
        pending = PendingSignup(
            email=email,
            tenant_name=body.tenant_name,
            password_hash=hash_password(body.password),
            credit_package_id=body.credit_package_id,
        )
        session.add(pending)
    else:
        # Um checkout já iniciado pode ser pago depois: não altere os dados
        # usados pelo webhook. Novo link apenas recupera o acesso ao pagamento.
        if pending.verified_at is None:
            pending.tenant_name = body.tenant_name
            pending.password_hash = hash_password(body.password)
            pending.credit_package_id = body.credit_package_id

    await _send_pending_verification(session, pending)


async def resend_verification(session: AsyncSession, email: str) -> None:
    if not settings.gmail_smtp_user or not settings.gmail_smtp_app_password:
        raise HTTPException(
            status_code=503, detail="Confirmação por e-mail indisponível no momento."
        )
    pending = await session.scalar(
        select(PendingSignup).where(PendingSignup.email == email.lower()).with_for_update()
    )
    # Não revele a terceiros se o endereço iniciou um cadastro.
    if pending is None or pending.completed_at is not None:
        return
    await _send_pending_verification(session, pending)


async def _send_pending_verification(session: AsyncSession, pending: PendingSignup) -> None:
    now = datetime.now(UTC)
    if pending.last_sent_at and now - pending.last_sent_at < RESEND_DELAY:
        raise HTTPException(status_code=429, detail="Aguarde um minuto antes de reenviar.")

    if (
        pending.send_window_started_at is None
        or now - pending.send_window_started_at >= SEND_WINDOW
    ):
        pending.send_window_started_at = now
        pending.send_count = 0
    if pending.send_count >= MAX_SENDS:
        raise HTTPException(
            status_code=429, detail="Limite de envios atingido. Tente novamente em uma hora."
        )

    token = secrets.token_urlsafe(32)
    pending.verification_token_hash = _digest(token)
    pending.verification_expires_at = now + VERIFY_TTL
    pending.send_count += 1
    pending.last_sent_at = now
    await session.commit()

    try:
        await asyncio.to_thread(_send_verification_sync, pending.email, token)
    except (OSError, smtplib.SMTPException) as exc:
        logger.warning("Falha no e-mail de confirmação | error_type=%s", type(exc).__name__)
        # Libera uma nova tentativa imediata quando o provedor não aceitou o
        # envio; o usuário não deve consumir o rate limit por uma falha nossa.
        if pending.verification_token_hash == _digest(token):
            pending.verification_token_hash = None
            pending.verification_expires_at = None
            pending.last_sent_at = None
            pending.send_count = max(pending.send_count - 1, 0)
            await session.commit()
        raise HTTPException(
            status_code=502, detail="Não foi possível enviar o e-mail. Tente novamente."
        ) from exc


async def verify_email(session: AsyncSession, token: str) -> str:
    now = datetime.now(UTC)
    pending = await session.scalar(
        select(PendingSignup)
        .where(PendingSignup.verification_token_hash == _digest(token))
        .with_for_update()
    )
    if (
        pending is None
        or pending.verification_expires_at is None
        or pending.verification_expires_at <= now
    ):
        raise HTTPException(
            status_code=400, detail="Link inválido ou expirado. Solicite outro cadastro."
        )
    if pending.completed_at is not None:
        raise HTTPException(status_code=400, detail="Cadastro já concluído.")

    checkout_token = secrets.token_urlsafe(32)
    pending.verification_token_hash = None
    pending.verification_expires_at = None
    pending.verified_at = now
    pending.checkout_token_hash = _digest(checkout_token)
    pending.checkout_expires_at = now + CHECKOUT_TTL
    await session.commit()
    return checkout_token


async def get_verified_signup(session: AsyncSession, checkout_token: str) -> PendingSignup:
    now = datetime.now(UTC)
    pending = await session.scalar(
        select(PendingSignup)
        .where(PendingSignup.checkout_token_hash == _digest(checkout_token))
        .with_for_update()
    )
    if (
        pending is None
        or pending.verified_at is None
        or pending.completed_at is not None
        or pending.checkout_expires_at is None
        or pending.checkout_expires_at <= now
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Confirme o e-mail antes de pagar."
        )
    return pending
