from __future__ import annotations

"""Regression for real Telegram Settings tab, enrollment and background actions."""
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from linkvideo_vpn_helper.services.telegram_invites import InviteStore
from linkvideo_vpn_helper.services.telegram_service_runtime import clean_bot_link
from linkvideo_vpn_helper.ui.telegram_settings_widget import TelegramSettingsWidget
from linkvideo_vpn_helper.ui.pages.settings_page import SettingsPage

app = QApplication.instance() or QApplication([])

assert clean_bot_link("@LinkVideoHelperBot") == "https://t.me/LinkVideoHelperBot"
assert clean_bot_link("https://t.me/LinkVideoHelperBot/") == "https://t.me/LinkVideoHelperBot"
for bad in ("", "https://evil.example/bot", "https://t.me/x?start=somecode"):
    try:
        clean_bot_link(bad)
        raise AssertionError("Invalid link accepted")
    except ValueError:
        pass

with tempfile.TemporaryDirectory() as td:
    database = Path(td) / "invites.sqlite"
    store = InviteStore(database)
    store.grant(123456789)
    assert InviteStore.list_members(database) == (123456789,)
    store.revoke(123456789)
    assert InviteStore.list_members(database) == ()

    sent = []
    def fake_admin_action(data):
        sent.append(dict(data))
        if data["operation"] == "invite":
            return "https://t.me/LinkVideoHelperBot?start=EXAMPLE"
        return "Сохранено"

    with patch("linkvideo_vpn_helper.ui.telegram_settings_widget.admin_action",
               side_effect=fake_admin_action), patch(
        "linkvideo_vpn_helper.ui.telegram_settings_widget.public_bot_link",
        return_value="https://t.me/LinkVideoHelperBot"
    ), patch(
        "linkvideo_vpn_helper.ui.telegram_settings_widget.configured",
        return_value=True
    ), patch(
        "linkvideo_vpn_helper.ui.telegram_settings_widget.is_task_installed",
        return_value=True
    ), patch(
        "linkvideo_vpn_helper.ui.telegram_settings_widget.InviteStore.list_members",
        return_value=(123456789,)
    ):
        ui = TelegramSettingsWidget()
        ui.url_input.setText("@LinkVideoHelperBot")
        ui.token_input.setText("123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        ui.save_button.click()
        deadline = time.monotonic() + 3
        while ui._busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not ui._busy and sent[-1]["operation"] == "configure"
        assert sent[-1]["link"] == "https://t.me/LinkVideoHelperBot"
        assert not ui.token_input.text(), "Bot token must not remain in GUI"
        ui.employee_id.setText("123456789")
        ui.add_button.click()
        while ui._busy and time.monotonic() < deadline + 3:
            app.processEvents()
            time.sleep(0.01)
        assert sent[-1] == {"operation": "add", "telegram_id": 123456789}
        ui.invite_button.click()
        while ui._busy and time.monotonic() < deadline + 6:
            app.processEvents()
            time.sleep(0.01)
        assert "?start=" in ui.generated_link.text()
        assert "123456789" in ui.employee_list.text()
        ui.refresh_button.click()
        deadline = time.monotonic() + 3
        while ui._busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert sent[-1] == {"operation": "probe"}
        ui.close()

    class Registry:
        def all(self, include_disabled=False):
            return []
    settings = QSettings(str(Path(td) / "settings.ini"), QSettings.Format.IniFormat)
    with patch("linkvideo_vpn_helper.ui.telegram_settings_widget.configured",
               return_value=False), patch(
        "linkvideo_vpn_helper.ui.telegram_settings_widget.is_task_installed",
        return_value=False
    ), patch(
        "linkvideo_vpn_helper.ui.telegram_settings_widget.public_bot_link",
        return_value=""
    ):
        page = SettingsPage(settings, Registry())
        assert page.settings_tabs.indexOf(page.telegram_tab) >= 0
        assert page.telegram_panel.url_input.objectName() == "TelegramBotLink"
        assert page.telegram_panel.token_input.objectName() == "TelegramBotToken"
        page.close()


# Verify bot token and saved link only against Telegram's getMe identity.
from linkvideo_vpn_helper.services.telegram_service_runtime import probe_telegram_bot
from io import BytesIO

class FakeResponse:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, max_bytes):
        return b'{"ok":true,"result":{"username":"LinkVideoHelperBot"}}'

with patch(
    "linkvideo_vpn_helper.services.telegram_service_runtime.service_token",
    return_value="123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
), patch(
    "linkvideo_vpn_helper.services.telegram_service_runtime.public_bot_link",
    return_value="https://t.me/LinkVideoHelperBot",
), patch(
    "urllib.request.urlopen", return_value=FakeResponse()
):
    assert "LinkVideoHelperBot" in probe_telegram_bot()
with patch(
    "linkvideo_vpn_helper.services.telegram_service_runtime.service_token",
    return_value="123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
), patch(
    "linkvideo_vpn_helper.services.telegram_service_runtime.public_bot_link",
    return_value="https://t.me/OtherTestBot",
), patch(
    "urllib.request.urlopen", return_value=FakeResponse()
):
    try:
        probe_telegram_bot()
        raise AssertionError("Mismatched bot link was accepted.")
    except RuntimeError:
        pass

print("CORE TESTS 3.0.16 TELEGRAM SETTINGS + WINDOWS TASK FRONTEND OK")
