import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.tasks import outbound_outbox


async def test_entrega_meta_de_texto_nao_chama_agents(monkeypatch) -> None:
    send_text = AsyncMock()
    monkeypatch.setattr(outbound_outbox, "send_text_message", send_text)
    monkeypatch.setattr(outbound_outbox, "decrypt_access_token", lambda value: f"claro:{value}")

    await outbound_outbox._send_delivery(
        SimpleNamespace(
            provider="meta",
            access_token_encrypted="cifrado",
            phone_number_id="PNID",
            contact_phone_number="5511999998888",
            content="Olá",
            media_url=None,
        )
    )

    send_text.assert_awaited_once_with("PNID", "claro:cifrado", "5511999998888", "Olá")


async def test_entrega_zapi_de_documento_mantem_nome(monkeypatch) -> None:
    send_document = AsyncMock()
    monkeypatch.setattr(outbound_outbox, "send_zapi_document_message", send_document)
    monkeypatch.setattr(outbound_outbox, "decrypt_access_token", lambda value: f"claro:{value}")

    await outbound_outbox._send_delivery(
        SimpleNamespace(
            provider="zapi",
            zapi_instance_token_encrypted="token",
            zapi_client_token_encrypted="client-token",
            zapi_instance_id="inst-1",
            contact_phone_number="5511999998888",
            content="📄 Contrato.pdf",
            media_url="https://agents.exemplo.com/documento.pdf",
        )
    )

    send_document.assert_awaited_once_with(
        "inst-1",
        "claro:token",
        "claro:client-token",
        "5511999998888",
        "https://agents.exemplo.com/documento.pdf",
        "Contrato.pdf",
    )


async def test_sem_redis_a_entrega_fica_disponivel_para_recuperacao() -> None:
    await outbound_outbox.enqueue_outbound_message_jobs({}, [])


async def test_cancela_entrega_quando_atendimento_nao_esta_com_ia() -> None:
    session = AsyncMock()
    state_result = MagicMock()
    state_result.scalar_one_or_none.return_value = "human"
    session.execute.side_effect = [state_result, MagicMock()]

    cancelled = await outbound_outbox._cancel_if_automation_paused(
        session, uuid.uuid4(), uuid.uuid4()
    )

    assert cancelled is True
    assert session.execute.await_count == 2
    update_statement = session.execute.await_args_list[1].args[0]
    compiled = str(update_statement.compile(compile_kwargs={"literal_binds": True}))
    assert "delivery_status='cancelled'" in compiled
    session.commit.assert_awaited_once()


async def test_mantem_entrega_quando_atendimento_esta_com_ia() -> None:
    session = AsyncMock()
    state_result = MagicMock()
    state_result.scalar_one_or_none.return_value = "agent"
    session.execute.return_value = state_result

    cancelled = await outbound_outbox._cancel_if_automation_paused(
        session, uuid.uuid4(), uuid.uuid4()
    )

    assert cancelled is False
    session.commit.assert_not_awaited()
