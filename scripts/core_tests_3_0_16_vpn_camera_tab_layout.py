from __future__ import annotations

"""Regression: a large camera inventory must not stretch the VPN workspace."""
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from linkvideo_vpn_helper.services.camera_lookup import CameraChoice
from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage

app = QApplication.instance() or QApplication([])

class FakeRegistry:
    def __init__(self, settings):
        self.settings = settings
    def all(self, include_disabled=False):
        return []
    def hosts(self):
        return []

with tempfile.TemporaryDirectory() as temp:
    settings = QSettings(str(Path(temp) / "layout.ini"), QSettings.Format.IniFormat)
    settings.setValue("ui/animations_enabled", False)
    registry = FakeRegistry(settings)
    page = SearchManagePage(
        service=SimpleNamespace(), search=SimpleNamespace(),
        credentials=SimpleNamespace(username="fixture", password="not-used"),
        registry=registry,
    )
    page.resize(1200, 850)
    page.show()
    app.processEvents()

    assert page.main_tabs.currentIndex() == 0
    assert page.main_tabs.maximumHeight() <= 900
    assert page.workspace.maximumHeight() <= 820
    assert page.detail_panel.maximumHeight() == 16777215

    cameras = tuple(
        CameraChoice(str(10000 + n), 241, "Россия", f"Камера {n}")
        for n in range(20)
    )
    page.main_tabs.setCurrentIndex(1)
    page.camera_diagnostics._on_result(0, cameras, "")
    app.processEvents()
    assert page.camera_diagnostics.list.count() == 20
    assert page.camera_diagnostics.list.height() <= 510
    assert page.camera_diagnostics.minimumHeight() == 0

    page.main_tabs.setCurrentIndex(0)
    app.processEvents()
    assert page.main_tabs.minimumHeight() == 720
    assert page.main_tabs.maximumHeight() == 900
    assert page.workspace.maximumHeight() == 820
    assert page.left_card.isVisible()
    # The list heading must remain at the TOP of the left card even after
    # opening the camera tab with 20 entries.
    heading = page.results_title.mapTo(page.left_card, page.results_title.rect().topLeft())
    assert 0 <= heading.y() < 130, ("VPN search heading moved", heading.y())

    page.current = SimpleNamespace(login="test-customer")
    with patch.object(page, "_render_client", return_value=None):
        page._open_client_view()
    app.processEvents()
    assert page.detail_panel.isVisible()
    assert page.client_info_card.isVisible()
    assert not page.left_card.isVisible()
    assert page.detail_panel.maximumHeight() == 16777215
    assert page.detail_panel.y() <= 45, ("Management panel fell below profile", page.detail_panel.y())
    assert page.client_info_card.y() <= 45

    page._close_client_view(immediate=True)
    app.processEvents()
    assert page.left_card.isVisible()
    page.close()

print("CORE TESTS 3.0.16 VPN/CAMERA TAB LAYOUT OK")
