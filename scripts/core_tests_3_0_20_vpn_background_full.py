from __future__ import annotations

"""Offline Windows tests for opt-in full VPN Sheets mirror via Task Scheduler."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from linkvideo_vpn_helper.services import vpn_background_service as bg
from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.ui.pages.settings_page import SettingsPage

app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as td:
    settings = QSettings(str(Path(td) / "settings.ini"), QSettings.Format.IniFormat)
    registry = ServerRegistry(settings)
    with patch.object(bg, "task_registered", return_value=False):
        page = SettingsPage(settings, registry)
    assert page.bg_enabled.isChecked() is False, "background mode must be OFF by default"
    assert settings.value("vpn_background/enabled", False, bool) is False
    assert "пароли PPP" in page.findChildren(type(page.bg_status))[0].text() or any(
        "пароли PPP" in label.text() for label in page.findChildren(type(page.bg_status))
    )
    assert "12" in page.bg_targets.text()
    # No Windows setup and no credentials -> toggling cannot silently enable.
    page.bg_enabled.setChecked(True)
    assert page.bg_enabled.isChecked() is False
    page.close()

assert bg.TASK_NAME == "LinkVideo.Helper.VPNSheetsMirror"
assert bg._INTERVAL == 5

with TemporaryDirectory() as td:
    credentials = SimpleNamespace(
        username="admin-test", password="ROUTEROS_PASSWORD_SHOULD_NOT_LEAK", port=8728, timeout=6,
    )
    google = {
        "type": "service_account", "client_email": "backup@example.invalid",
        "private_key": "GOOGLE_PRIVATE_KEY_SHOULD_NOT_LEAK", "token_uri": "https://oauth2.googleapis.com/token",
    }
    # On Windows CI, exercise real native DPAPI instead of a fake reversible cipher.
    with patch.dict(os.environ, {"LOCALAPPDATA": td}):
        stage = bg.stage_configuration(
            credentials, google,
            ["vpn01.linkvideo.ru", "vpn02.linkvideo.ru"],
            "1KxIMsVOtDD8klpVUj_vymbtZIDSkvS9-vjT5cQ2a9eA",
        )
        content = stage.read_text(encoding="utf-8")
        assert "ROUTEROS_PASSWORD_SHOULD_NOT_LEAK" not in content
        assert "GOOGLE_PRIVATE_KEY_SHOULD_NOT_LEAK" not in content
        restored = bg._unpack_settings(json.loads(content))
        assert restored["routeros_password"] == credentials.password
        assert restored["google_service_account"]["private_key"] == google["private_key"]
        assert restored["servers"] == ["vpn01.linkvideo.ru", "vpn02.linkvideo.ru"]
        stage.unlink()
        assert not stage.exists()

source = (
    Path(__file__).resolve().parents[1]
    / "linkvideo_vpn_helper/services/vpn_background_service.py"
).read_text(encoding="utf-8")
app_source = (
    Path(__file__).resolve().parents[1] / "linkvideo_vpn_helper/app.py"
).read_text(encoding="utf-8")
for term in (
    "CryptProtectData", "CryptUnprotectData",
    "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F",
    "schtasks.exe", '"/RU", "SYSTEM"',
    '"/SC", "MINUTE"', '"/MO", str(_INTERVAL)',
    "--vpn-background-worker",
    "install_vpn_sheets_retention_compat()",
    "install_vpn_sheets_resilience()",
    "install_vpn_sheets_operator_view()",
    "service.sync_server(",
    'source="LinkVideo.Helper background"',
):
    assert term in source, term
assert "dispatch_command(sys.argv[1:])" in app_source
assert app_source.index("dispatch_command(sys.argv[1:])") < app_source.index("app = QApplication(sys.argv)")
assert "routeros_password" not in str(bg.TASK_NAME).lower()
assert "Пароль" in "Пароль PPP"

print("CORE TESTS 3.0.20 WINDOWS FULL VPN BACKGROUND MIRROR (OPT-IN, DPAPI, TASK) OK")
