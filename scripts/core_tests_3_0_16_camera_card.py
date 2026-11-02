from __future__ import annotations
"""Offline check of customer camera selection and bounded bitrate parsing."""
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QWidget, QDialog, QPushButton
from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.billing_gateway import BillingClient, BillingService
from linkvideo_vpn_helper.services.camera_lookup import (
    CameraChoice, search_by_login, search_all_cameras,
)
from linkvideo_vpn_helper.services.camera_live_probe import parse_recent_segment, probe_live_camera
from linkvideo_vpn_helper.ui.camera_detail_dialog import CameraDetailDialog
from linkvideo_vpn_helper.ui.camera_diagnostics_panel import CameraDiagnosticsPanel
from linkvideo_vpn_helper.ui.first_page_window_guard import FirstPageWindowGuard

app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as td:
    settings = QSettings(str(Path(td) / "camera.ini"), QSettings.Format.IniFormat)
    b2o = B2OService(settings)
    b2o.token = lambda: "fixture-session"
    b2o.request_json = lambda *args, **kw: {"data": [
        {"account_login": "other", "id_camera": 99},
        {"account_login": "customer.demo", "id_camera": 12345}
    ]}
    service = BillingService(
        id="66", camera_name="Парковка", camera_id="66",
        tariff_name="30 дней", price_text="", discount_text="", total_text="",
        status="Активный", camera_vc_id="777",
    )
    client = BillingClient(
        id="45", login="customer.demo", country="Беларусь",
        status="Активен", services=(service,),
    )
    class Gateway:
        def search_clients(self, query):
            return (SimpleNamespace(login=query),)
        def find_client_by_hit(self, hit):
            return client
    creds = SimpleNamespace(username="demo", password="fixture-only")
    choices = search_by_login(
        "customer.demo", b2o, creds, crm_factory=lambda _: Gateway()
    )
    assert {(x.operator_id, x.camera_id) for x in choices} == {
        (1741, "777"), (241, "12345")
    }
    # A numeric serial is NOT automatically a camera ID. Show only actual
    # B2O cameras verified through detail or exact serial matching.
    def verified_detail(_service, value, operator, **kwargs):
        if str(value) not in ("12345", "linkvideo_12345") or operator != 241:
            raise LookupError("not an existing ID")
        return SimpleNamespace(
            camera_id="linkvideo_12345", name="Главный вход",
            status="online", metadata={"id_camera": 12345},
        )

    serial_camera = search_all_cameras(
        "89130903662", b2o, creds, detail_reader=verified_detail,
        serial_resolver=lambda serial, token, service:
            ("Россия", "12345") if serial == "89130903662" else None,
        login_search=lambda *args: (),
    )
    assert len(serial_camera) == 1
    assert serial_camera[0].camera_id == "12345"
    assert serial_camera[0].label == "Главный вход"
    assert search_all_cameras(
        "98765432101", b2o, creds, detail_reader=verified_detail,
        serial_resolver=lambda *args: None, login_search=lambda *args: (),
    ) == ()
    login_cameras = search_all_cameras(
        "customer.demo", b2o, creds,
        serial_resolver=lambda *args: None,
        login_search=lambda *args: choices,
    )
    assert len(login_cameras) == 2

    panel = CameraDiagnosticsPanel(
        settings, customer_search=lambda *args: login_cameras,
        preview_loader=lambda country, number, token: b"",
    )
    assert not hasattr(panel, "mode")
    assert not hasattr(panel, "operator")
    panel._on_result(0, login_cameras, "")
    assert panel.list.count() == 2
    first = panel.list.itemWidget(panel.list.item(0))
    assert first is not None
    assert first.findChild(QPushButton, "CameraSearchOpen") is not None
    assert panel._thumb_labels[0].size().width() == 160
    panel.close()


    # Signed recent DVR is the only bitrate fallback when B2O has no explicit
    # live playlist. The current tariff and a TCP handshake cannot measure it.
    from linkvideo_vpn_helper.services.camera_diagnostics import CameraDiagnostics
    from linkvideo_vpn_helper.services.camera_live_probe import (
        _recent_archive_playlists,
    )
    signed = CameraDiagnostics(
        "linkvideo_12345", "b2o-vcore38.video.goodline.info", "",
        554, "", "", metadata={
            "signature": "fixture-signature",
            "main_stream_name": "linkvideo_12345",
        },
    )
    candidates = _recent_archive_playlists(signed)
    assert candidates and "playlist_dvr_range-" in candidates[0]
    assert "wmsAuthSign=fixture-signature" in candidates[0]

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload
        def __enter__(self):
            return self
        def __exit__(self, *unused):
            return False
        def read(self, max_bytes):
            return self.payload[:max_bytes]
    class FakeOpen:
        def open(self, request, timeout=5):
            if request.full_url.split("?", 1)[0].endswith(".m3u8"):
                return FakeResponse(b"#EXTM3U\n#EXTINF:2.0,\nsegment.ts\n")
            if request.full_url.endswith("segment.ts"):
                return FakeResponse(b"x" * 500000)
            raise AssertionError("Unexpected media request")
    with patch("linkvideo_vpn_helper.services.camera_live_probe._ping_windows",
               return_value="ICMP: ответ получен."), patch(
        "linkvideo_vpn_helper.services.camera_live_probe._test_tcp",
        return_value="TCP:443 — открыт с этого компьютера."):
        health = probe_live_camera(signed, opener=FakeOpen())
    assert "2.00" in health.bitrate_mbps
    assert "архив" in health.bitrate_mbps
    unsigned = CameraDiagnostics(
        "linkvideo_12345", "b2o-vcore38.video.goodline.info", "",
        554, "", "", metadata={"main_stream_name": "linkvideo_12345"},
    )
    assert _recent_archive_playlists(unsigned) == ()
    assert probe_live_camera(unsigned, opener=FakeOpen()).bitrate_mbps == "Не измерен"

    playlist = "#EXTM3U\n#EXTINF:2.0,\nsegment.ts\n"
    base = "https://b2o-vcore77.video.goodline.info/live/test.m3u8"
    segment = parse_recent_segment(playlist, base)
    assert segment and segment[1] == 2
    with patch.object(CameraDetailDialog, "refresh", return_value=None):
        dialog = CameraDetailDialog(choices[0], settings)
        assert dialog.rate is not None
        assert dialog.image is not None
        dialog.close()

    guard = FirstPageWindowGuard()
    app.installEventFilter(guard)
    orphan = QWidget()
    orphan.show()
    assert orphan.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dialog2 = QDialog()
    dialog2.show()
    assert not dialog2.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    host = QWidget()
    orphan.setParent(host)
    app.removeEventFilter(guard)
    guard.release()
    assert not orphan.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dialog2.close()
    orphan.close()
    host.close()
print("CORE TESTS CAMERA CUSTOMER CARD OK")
