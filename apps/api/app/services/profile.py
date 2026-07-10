"""Atualização de dados do escritório e troca de senha do usuário logado."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models import Tenant, User


class InvalidCurrentPasswordError(Exception):
    """Senha atual não confere — mapeada para 400 na rota."""


async def update_tenant_name(session: AsyncSession, tenant_id: uuid.UUID, name: str) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    tenant.name = name
    await session.commit()
    return tenant


async def change_password(
    session: AsyncSession, user_id: uuid.UUID, current_password: str, new_password: str
) -> None:
    user = await session.get(User, user_id)
    if not verify_password(current_password, user.password_hash):
        raise InvalidCurrentPasswordError("Senha atual incorreta")
    user.password_hash = hash_password(new_password)
    await session.commit()
