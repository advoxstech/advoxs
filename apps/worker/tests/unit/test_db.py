from unittest.mock import AsyncMock, MagicMock

from app.db import open_tenant_session


async def test_seta_app_tenant_id_e_produz_a_sessao_do_factory() -> None:
    session = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    async with open_tenant_session(factory, "tenant-123") as yielded:
        assert yielded is session

    session.execute.assert_awaited_once()
    call = session.execute.await_args
    assert "set_config" in str(call.args[0])
    assert call.args[1] == {"tenant_id": "tenant-123"}
