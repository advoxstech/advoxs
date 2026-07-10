from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# advoxs_app — RLS ativo, usado pelas rotas tenant-scoped (via
# get_tenant_session, em app/api/deps.py) e nunca diretamente por rota
# nenhuma sem antes setar app.tenant_id.
engine = create_async_engine(settings.app_database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# advoxs_system — BYPASSRLS, usado pelas rotas genuinamente cross-tenant
# (login, webhooks, idempotência de pagamento, painel de admin).
system_engine = create_async_engine(settings.system_database_url, pool_pre_ping=True)
SystemSessionLocal = async_sessionmaker(system_engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_system_session() -> AsyncIterator[AsyncSession]:
    async with SystemSessionLocal() as session:
        yield session
