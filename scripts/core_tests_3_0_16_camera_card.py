from __future__ import annotations
"""Offline check of customer camera selection and bounded bitrate parsing."""
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QWidget, QDialog
from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.billing_gateway import BillingClient, BillingService
from linkvideo_vpn_helper.services.camera_lookup import CameraChoice, search_by_login
from linkvideo_vpn_helper.services.camera_live_probe import parse_recent_segment
from linkvideo_vpn_helper.ui.camera_detail_dialog import CameraDetailDialog
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
            return (query,)
        def find_client_by_hit(self, hit):
            return client
    creds = SimpleNamespace(username="demo", password="fixture-only")
    choices = search_by_login(
        "customer.demo", b2o, creds, crm_factory=lambda _: Gateway()
    )
    assert {(x.operator_id, x.camera_id) for x in choices} == {
        (1741, "777"), (241, "12345")
    }
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
