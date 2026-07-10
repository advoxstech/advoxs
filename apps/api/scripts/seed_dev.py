"""Seed de desenvolvimento: cria tenant + usuário admin + número WhatsApp.

Permite exercitar o fluxo ponta a ponta (login e webhook → agents) enquanto o
Embedded Signup não existe. Idempotente por e-mail/phone_number_id (não duplica).

Uso (dentro de apps/api, com DATABASE_URL e WHATSAPP_TOKEN_ENCRYPTION_KEY no ambiente):

    uv run python scripts/seed_dev.py \
        --tenant-name "Escritório Demo" \
        --email admin@demo.com --password segredo123 \
        --phone-number-id 123456789 --waba-id 987654321 \
        --display-phone "+55 11 99999-8888" --access-token EAAG...
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Executável como `python scripts/seed_dev.py` de qualquer cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.crypto import encrypt_access_token
from app.core.db import SystemSessionLocal
from app.core.security import hash_password
from app.models import Tenant, User, WhatsAppNumber


async def seed(args: argparse.Namespace) -> None:
    async with SystemSessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == args.email))
        if user is not None:
            tenant = await session.get(Tenant, user.tenant_id)
            print(f"Usuário {args.email} já existe (tenant {tenant.id}) — nada a criar.")
        else:
            tenant = Tenant(name=args.tenant_name, email_contato=args.email)
            session.add(tenant)
            await session.flush()

            session.add(
                User(
                    tenant_id=tenant.id,
                    name=args.user_name,
                    email=args.email,
                    password_hash=hash_password(args.password),
                )
            )
            print(f"Tenant {tenant.id} + usuário {args.email} criados.")

        if args.phone_number_id:
            existing = await session.scalar(
                select(WhatsAppNumber).where(WhatsAppNumber.phone_number_id == args.phone_number_id)
            )
            if existing is not None:
                print(f"phone_number_id {args.phone_number_id} já registrado — pulando.")
            else:
                session.add(
                    WhatsAppNumber(
                        tenant_id=tenant.id,
                        phone_number_id=args.phone_number_id,
                        waba_id=args.waba_id,
                        display_phone_number=args.display_phone,
                        access_token_encrypted=encrypt_access_token(args.access_token),
                    )
                )
                print(f"Número WhatsApp {args.display_phone} vinculado ao tenant {tenant.id}.")

        await session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--user-name", default="Admin")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--phone-number-id", default=None)
    parser.add_argument("--waba-id", default="")
    parser.add_argument("--display-phone", default="")
    parser.add_argument("--access-token", default="")

    args = parser.parse_args()
    if args.phone_number_id and not args.access_token:
        parser.error("--access-token é obrigatório quando --phone-number-id é informado")

    asyncio.run(seed(args))


if __name__ == "__main__":
    main()
