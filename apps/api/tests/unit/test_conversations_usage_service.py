import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

from app.services.conversations_usage import build_conversations_usage

TENANT_ID = uuid.uuid4()


def _execute_result(rows: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


async def test_mapeia_linhas_agregadas_para_o_schema() -> None:
    session = AsyncMock()
    conv_id = uuid.uuid4()
    session.execute.return_value = _execute_result(
        [(conv_id, "5511999990001", False, 12.5, 3, datetime(2026, 7, 15, tzinfo=UTC))]
    )

    result = await build_conversations_usage(
        session, TENANT_ID, date(2026, 7, 1), date(2026, 7, 17), 50, 0
    )

    assert len(result) == 1
    assert result[0].conversation_id == conv_id
    assert result[0].contact_phone_number == "5511999990001"
    assert result[0].is_test is False
    assert result[0].credits_consumed == 12.5
    assert result[0].billed_responses == 3


async def test_sem_linhas_retorna_lista_vazia() -> None:
    session = AsyncMock()
    session.execute.return_value = _execute_result([])

    result = await build_conversations_usage(
        session, TENANT_ID, date(2026, 7, 1), date(2026, 7, 17), 50, 0
    )

    assert result == []


async def test_query_filtra_tenant_credits_consumed_not_null_e_periodo() -> None:
    session = AsyncMock()
    session.execute.return_value = _execute_result([])

    await build_conversations_usage(session, TENANT_ID, date(2026, 7, 1), date(2026, 7, 17), 50, 0)

    query = session.execute.call_args.args[0]
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "tenant_id" in compiled
    assert "credits_consumed IS NOT NULL" in compiled
    assert "2026-07-01" in compiled
    assert "2026-07-17" in compiled
