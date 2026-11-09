from __future__ import annotations

"""Offline regression checks for account menus, About history and B2O hosts."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel

from linkvideo_vpn_helper.release_notes import RELEASE_HISTORY, release_notes
from linkvideo_vpn_helper.ui.pages.settings_page import SettingsPage


assert RELEASE_HISTORY[0][0] == "3.0.18"
assert "FFmpeg" in " ".join(release_notes("3.0.18"))
assert all(release_notes(version) for version, _ in RELEASE_HISTORY)

app = QApplication.instance() or QApplication([])

with TemporaryDirectory() as tmp:
    settings = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
    registry = SimpleNamespace(all=lambda include_disabled=True: [])
    page = SettingsPage(settings, registry)
    assert len(page.archive_host_rows) == 3
    assert page.archive_b2o.custom_archive_servers(1721) == []

    with patch(
        "linkvideo_vpn_helper.ui.pages.settings_page.QInputDialog.getText",
        return_value=("kz-vcore77.video.goodline.info", True),
    ):
        page._add_archive_host(1721)
    assert page.archive_b2o.custom_archive_servers(1721) == [
        "kz-vcore77.video.goodline.info"
    ]
    # Other operators remain unaffected.
    assert page.archive_b2o.custom_archive_servers(241) == []
    assert page.archive_b2o.custom_archive_servers(1741) == []

    with patch(
        "linkvideo_vpn_helper.ui.pages.settings_page.QInputDialog.getText",
        return_value=("https://wrong.example.com/api?token=secret", True),
    ):
        page._add_archive_host(241)
    assert page.archive_b2o.custom_archive_servers(241) == []

    page.archive_b2o.remove_custom_archive_server(1721, "kz-vcore77.video.goodline.info")
    assert not page.archive_b2o.custom_archive_servers(1721)
    page._render_archive_hosts(1721)

    about = page.about_tab.findChildren(QLabel)
    assert any("Что нового в 3.0.18" in label.text() for label in about)
    assert any("История изменений" in label.text() for label in about)
    page.close()

source = (
    Path(__file__).resolve().parents[1]
    / "linkvideo_vpn_helper/ui/main_window.py"
).read_text(encoding="utf-8")
assert "self.admin_account_panel = QMenu(self)" in source
assert "self.work_account_panel = QMenu(self)" in source
assert "sl.addWidget(self.admin_account_panel)" not in source
assert "sl.addWidget(self.work_account_panel)" not in source

print("CORE TESTS 3.0.18 SETTINGS RELEASE NOTES / B2O SERVERS / ACCOUNT MENUS OK")
