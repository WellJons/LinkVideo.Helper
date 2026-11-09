from __future__ import annotations

"""Offscreen regression: B2O camera IDs must survive the actual styled port rows.

The normal search page builds QListWidgetItem text, but startup then installs
nat_counter_integration, which blanks that text and paints child widgets. This
test must execute the startup integration, not merely inspect source strings.
"""
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel
from linkvideo_vpn_helper.services.vpn_service import ClientRecord
from linkvideo_vpn_helper.services.vpn_camera_port_match import (
    CameraPortLink, CameraPortReport,
)
from linkvideo_vpn_helper.ui.nat_counter_integration import install_nat_counter_ui
from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage


class FakeRegistry:
    def __init__(self, settings):
        self.settings = settings

    def all(self, include_disabled=False):
        return []

    def hosts(self):
        return []


app = QApplication.instance() or QApplication([])
install_nat_counter_ui()

with tempfile.TemporaryDirectory() as temp:
    settings = QSettings(str(Path(temp) / "port_camera.ini"), QSettings.Format.IniFormat)
    settings.setValue("ui/animations_enabled", False)
    page = SearchManagePage(
        service=SimpleNamespace(), search=SimpleNamespace(),
        credentials=SimpleNamespace(username="fixture", password="fixture"),
        registry=FakeRegistry(settings),
    )
    client = ClientRecord(
        server="vpn02.linkvideo.ru", login="89530936836",
        password="test", remote_address="172.16.0.10",
        ports=[10714, 10715], active_ports=[10714, 10715],
    )
    client.port_nat_bytes = {10714: 717414, 10715: 1468000}
    client.port_nat_packets = {10714: 11921, 10715: 23918}
    page.current = client
    page._camera_match_key = (
        client.server, client.login, tuple(sorted(client.ports)), client.login,
    )
    page._camera_match_result = CameraPortReport(
        login=client.login,
        links=(
            CameraPortLink(10714, "243001", "Вход", "Россия"),
            CameraPortLink(10715, "243002", "Парковка", "Россия"),
        ),
        ambiguous=(), unmatched=(), checked_cameras=2,
        stream_cameras=2,
    )
    page._camera_match_message = (
        "B2O: 2 камер · RTSP-ссылок с портом: 2 · совпадений: 2."
    )
    page._render_client()
    app.processEvents()

    assert page.port_list.count() == 2
    for number, identifier in ((10714, "243001"), (10715, "243002")):
        row = next(
            page.port_list.itemWidget(page.port_list.item(i))
            for i in range(page.port_list.count())
            if page.port_list.item(i).data(256) == number
        )
        assert row is not None, f"port {number} is missing styled widget"
        labels = row.findChildren(QLabel)
        values = [lbl.text() for lbl in labels]
        assert f"ID камеры B2O: {identifier}" in values, values
        assert any("NAT-трафик:" in value for value in values), values
        outer = row.layout()
        assert outer.itemAt(1).widget().text() == f"ID камеры B2O: {identifier}"
        assert page.port_list.height() >= 185

    # A stale lookup must NOT expose a camera ID for another VPN account.
    page._camera_match_key = (
        client.server, "different-account", tuple(sorted(client.ports)), "other",
    )
    page._render_client()
    app.processEvents()
    for i in range(page.port_list.count()):
        row = page.port_list.itemWidget(page.port_list.item(i))
        assert row is not None
        assert not any("ID камеры B2O:" in label.text() for label in row.findChildren(QLabel))

    page.close()

print("CORE TESTS 3.0.18 B2O CAMERA IDS VISIBLE IN STYLED PORT ROWS OK")
