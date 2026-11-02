from __future__ import annotations

"""Offline regression for customer lookup latency and intermittent telemetry."""
import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.camera_lookup import (
    search_all_cameras, search_by_login, CameraChoice,
)
from linkvideo_vpn_helper.services.camera_diagnostics import CameraDiagnostics
from linkvideo_vpn_helper.services.camera_live_probe import probe_live_camera
from linkvideo_vpn_helper.ui.camera_detail_dialog import CameraDetailDialog

app = QApplication.instance() or QApplication([])


class FakeB2O:
    normalize_camera_id = B2OService.normalize_camera_id
    operator_country = B2OService.operator_country
    operator_prefix = B2OService.operator_prefix
    resolve_operator_id = B2OService.resolve_operator_id
    CAMERAS_URL = B2OService.CAMERAS_URL
    calls = []
    def token(self):
        return "fixture-token"
    def request_json(self, url, token, headers=None, timeout=12):
        self.calls.append((url, dict(headers or {})))
        assert token == "fixture-token"
        return {"data": [
            {"account_login": "not-the-user", "id_camera": 99},
            {"account_login": "89130903662", "id_camera": 247626,
             "camera_name": "Зал", "camera_status": "online"},
            {"account_login": "89130903662", "id_camera": 251083,
             "camera_name": "Склад", "camera_status": "error"},
        ]}


fake = FakeB2O()
creds = SimpleNamespace(username="admin", password="fixture-pass")
def no_crm(*args, **kwargs):
    raise AssertionError("RU phone login must not wait for CRM authorization")
items = search_by_login("89130903662", fake, creds, crm_factory=no_crm)
assert len(items) == 2
assert len(fake.calls) == 1 and fake.calls[0][1]["Page"] == "1"
assert all(c.customer_login == "89130903662" for c in items)


def check_id(*args, **kwargs):
    raise AssertionError("Phone login must not be checked as a camera ID")
def check_serial(*args, **kwargs):
    raise AssertionError("Phone login results must not trigger serial scans")

fake.calls.clear()
found = search_all_cameras(
    "89130903662", fake, creds,
    login_search=lambda query, b2o, credentials:
        search_by_login(query, b2o, credentials, crm_factory=no_crm),
    detail_reader=check_id,
    serial_resolver=check_serial,
)
assert len(found) == 2 and len(fake.calls) == 1

# A proven recent DVR fragment has a bounded time range even when the server
# does not include EXT-X-PROGRAM-DATE-TIME. It is NOT an exact last-frame time.
report = CameraDiagnostics(
    camera_id="linkvideo_247626", server="", ip="", rtsp_port=0,
    ping_text="", tcp_text="", last_stream="",
)
with patch("linkvideo_vpn_helper.services.camera_live_probe._recent_archive_sample",
           return_value=(1.15, "", False)):
    data = probe_live_camera(report)
assert data.bitrate_mbps == "≈1.15 Мбит/с (архив)"
assert "последние 90 с" in data.last_stream
assert "точное время неизвестно" in data.last_stream

choice = CameraChoice("247626", 241, "Россия", "Зал")
with patch.object(CameraDetailDialog, "refresh", return_value=None):
    dialog = CameraDetailDialog(choice, SimpleNamespace())
    prior = CameraDiagnostics(
        camera_id="linkvideo_247626", server="", ip="", rtsp_port=0,
        ping_text="", tcp_text="", name="Зал", status="error",
    )
    with patch("linkvideo_vpn_helper.ui.camera_detail_dialog.threading.Thread.start",
               return_value=None):
        dialog._on_details((0, prior), "")
    assert "Камера не в сети" in dialog.status.text()
    before = dialog.info.text()
    assert "Зал" in before
    dialog._on_details((0, None), "Temporary B2O failure")
    assert dialog.info.text() == before
    assert "Обновление не удалось" in dialog.status.text()
    dialog.close()

print("CORE TESTS 3.0.16 FAST CAMERA LOOKUP AND STALE-STATE HANDLING OK")
