from datetime import UTC, datetime
from unittest.mock import MagicMock

import app.services.email_notifications as email_notifications_module
from app.services.email_notifications import send_zapi_request_notification

REQUESTED_AT = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _configure_gmail(monkeypatch) -> None:
    monkeypatch.setattr(email_notifications_module.settings, "gmail_smtp_user", "advoxs@gmail.com")
    monkeypatch.setattr(
        email_notifications_module.settings, "gmail_smtp_app_password", "senha-de-app"
    )
    monkeypatch.setattr(
        email_notifications_module.settings, "admin_notification_email", "advoxs@gmail.com"
    )


class TestSendZApiRequestNotification:
    async def test_pula_envio_quando_gmail_nao_configurado(self, monkeypatch) -> None:
        monkeypatch.setattr(email_notifications_module.settings, "gmail_smtp_user", "")
        monkeypatch.setattr(email_notifications_module.settings, "gmail_smtp_app_password", "")
        monkeypatch.setattr(email_notifications_module.settings, "admin_notification_email", "")
        send_mock = MagicMock()
        monkeypatch.setattr(email_notifications_module, "_send_email_sync", send_mock)

        await send_zapi_request_notification("Escritório X", REQUESTED_AT)

        send_mock.assert_not_called()

    async def test_envia_com_os_dados_certos_quando_configurado(self, monkeypatch) -> None:
        _configure_gmail(monkeypatch)
        send_mock = MagicMock()
        monkeypatch.setattr(email_notifications_module, "_send_email_sync", send_mock)

        await send_zapi_request_notification("Escritório X", REQUESTED_AT)

        send_mock.assert_called_once()
        to_address, subject, body = send_mock.call_args.args
        assert to_address == "advoxs@gmail.com"
        assert "Escritório X" in subject
        assert "Escritório X" in body
        assert "12/08/2026" in body

    async def test_falha_no_envio_nao_propaga(self, monkeypatch) -> None:
        _configure_gmail(monkeypatch)
        monkeypatch.setattr(
            email_notifications_module,
            "_send_email_sync",
            MagicMock(side_effect=OSError("Gmail indisponível")),
        )

        await send_zapi_request_notification("Escritório X", REQUESTED_AT)
