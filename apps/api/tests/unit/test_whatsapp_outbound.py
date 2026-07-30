from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.whatsapp_outbound as whatsapp_outbound_module
from app.clients.whatsapp import WhatsAppSendError
from app.clients.zapi import ZApiApiError, ZApiNetworkError
from app.services.whatsapp_outbound import send_text_to_contact


def _meta_number() -> SimpleNamespace:
    return SimpleNamespace(
        provider="meta",
        phone_number_id="PNID",
        access_token_encrypted="cifrado-meta",
    )


def _zapi_number(client_token_encrypted: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        provider="zapi",
        zapi_instance_id="inst-123",
        zapi_instance_token_encrypted="cifrado-token",
        zapi_client_token_encrypted=client_token_encrypted,
    )


class TestSendTextToContactRoutingMeta:
    async def test_provider_meta_chama_send_text_message_com_token_descriptografado(
        self, monkeypatch
    ) -> None:
        meta_send = AsyncMock()
        zapi_send = AsyncMock()
        monkeypatch.setattr(whatsapp_outbound_module, "send_text_message", meta_send)
        monkeypatch.setattr(whatsapp_outbound_module, "send_zapi_text_message", zapi_send)
        monkeypatch.setattr(
            whatsapp_outbound_module, "decrypt_access_token", lambda v: f"claro:{v}"
        )

        await send_text_to_contact(_meta_number(), to="5511999998888", text="Olá")

        meta_send.assert_awaited_once_with(
            phone_number_id="PNID",
            access_token="claro:cifrado-meta",
            to="5511999998888",
            text="Olá",
        )
        zapi_send.assert_not_awaited()


class TestSendTextToContactRoutingZApi:
    async def test_provider_zapi_chama_send_zapi_text_message(self, monkeypatch) -> None:
        meta_send = AsyncMock()
        zapi_send = AsyncMock()
        monkeypatch.setattr(whatsapp_outbound_module, "send_text_message", meta_send)
        monkeypatch.setattr(whatsapp_outbound_module, "send_zapi_text_message", zapi_send)
        monkeypatch.setattr(
            whatsapp_outbound_module, "decrypt_access_token", lambda v: f"claro:{v}"
        )

        await send_text_to_contact(
            _zapi_number(client_token_encrypted="cifrado-client"),
            to="5511999998888",
            text="Olá",
        )

        zapi_send.assert_awaited_once_with(
            instance_id="inst-123",
            token="claro:cifrado-token",
            client_token="claro:cifrado-client",
            to="5511999998888",
            text="Olá",
        )
        meta_send.assert_not_awaited()

    async def test_provider_zapi_sem_client_token_passa_none(self, monkeypatch) -> None:
        zapi_send = AsyncMock()
        monkeypatch.setattr(whatsapp_outbound_module, "send_zapi_text_message", zapi_send)
        monkeypatch.setattr(
            whatsapp_outbound_module, "decrypt_access_token", lambda v: f"claro:{v}"
        )

        await send_text_to_contact(_zapi_number(), to="5511999998888", text="Olá")

        assert zapi_send.await_args.kwargs["client_token"] is None

    async def test_erro_de_rede_da_zapi_vira_whatsapp_send_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            whatsapp_outbound_module,
            "send_zapi_text_message",
            AsyncMock(side_effect=ZApiNetworkError("timeout")),
        )
        monkeypatch.setattr(whatsapp_outbound_module, "decrypt_access_token", lambda v: v)

        with pytest.raises(WhatsAppSendError):
            await send_text_to_contact(_zapi_number(), to="5511999998888", text="Olá")

    async def test_erro_de_api_da_zapi_vira_whatsapp_send_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            whatsapp_outbound_module,
            "send_zapi_text_message",
            AsyncMock(side_effect=ZApiApiError("número inválido")),
        )
        monkeypatch.setattr(whatsapp_outbound_module, "decrypt_access_token", lambda v: v)

        with pytest.raises(WhatsAppSendError, match="número inválido"):
            await send_text_to_contact(_zapi_number(), to="5511999998888", text="Olá")
