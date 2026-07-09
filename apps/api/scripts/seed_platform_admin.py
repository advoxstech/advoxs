"""Seed de um platform_admin (back-office da Advoxs — nunca pertence a um tenant).

Uso (dentro de apps/api, com DATABASE_URL no ambiente):

    uv run python scripts/seed_platform_admin.py \
        --name "Falcão" --email falcao@advoxs.com.br --password segredo123
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import PlatformAdmin


async def seed(args: argparse.Namespace) -> None:
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(PlatformAdmin).where(PlatformAdmin.email == args.email)
        )
        if existing is not None:
            print(f"platform_admin {args.email} já existe — nada a criar.")
            return

        session.add(
            PlatformAdmin(
                name=args.name, email=args.email, password_hash=hash_password(args.password)
            )
        )
        await session.commit()
        print(f"platform_admin {args.email} criado.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    asyncio.run(seed(parser.parse_args()))


if __name__ == "__main__":
    main()
