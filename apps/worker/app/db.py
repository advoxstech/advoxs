from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


def create_engine_and_factory() -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(settings.app_database_url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def open_tenant_session(session_factory, tenant_id: str) -> AsyncIterator[AsyncSession]:
    """Abre uma sessão e seta app.tenant_id — ativa a RLS pro papel advoxs_app.

    Mesma mecânica de get_tenant_session (apps/api/app/api/deps.py):
    set_config com is_local=true vale só pra transação atual. Todo job do
    worker já recebe tenant_id como parâmetro de entrada, então setar o
    contexto aqui é direto — sem a complicação de "resolver o tenant no
    meio do caminho" que existe nos webhooks do api.
    """
    async with session_factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        yield session
