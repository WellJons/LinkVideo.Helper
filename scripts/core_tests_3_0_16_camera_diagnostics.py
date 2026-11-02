from __future__ import annotations
"""Offline camera diagnostics and B2O server-discovery regression tests."""
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from linkvideo_vpn_helper.services.archive_service_core import B2OService
from linkvideo_vpn_helper.services.archive_service import ArchiveService
from linkvideo_vpn_helper.services.camera_diagnostics import inspect_camera
from linkvideo_vpn_helper.ui.camera_diagnostics_panel import CameraDiagnosticsPanel

class FakeB2O:
    CAMERA_URL = B2OService.CAMERA_URL
    normalize_camera_id = B2OService.normalize_camera_id
    resolve_operator_id = B2OService.resolve_operator_id
    operator_country = B2OService.operator_country
    normalize_vcore_host = B2OService.normalize_vcore_host
    token = lambda self: "synthetic-token"

    def request_json(self, url, token, headers=None, timeout=15):
        assert url.endswith("/operators/241/cameras/12345")
        assert token == "synthetic-token"
        # Deliberately lacks signature, main_stream_name and server_name:
        # archive retrieval must not be a prerequisite for camera information.
        return {"data": {
            "id_camera": 12345,
            "camera_name": "Вход",
            "camera_status": "online",
            "camera_ip": "192.0.2.42",
            "rtsp_port": "8554",
            "serial_number": "SN123",
            "archive_depth": 7,
        }}

with patch("linkvideo_vpn_helper.services.camera_diagnostics._ping_windows",
           return_value="ICMP: ответ получен.") as ping, patch(
        "linkvideo_vpn_helper.services.camera_diagnostics._test_tcp",
        return_value="TCP:8554 — открыт с этого компьютера.") as tcp:
    report = inspect_camera(FakeB2O(), "12345", 241)
    assert report.camera_id == "linkvideo_12345"
    assert report.name == "Вход" and report.status == "online"
    assert report.server == ""  # Missing DVR is fine for read-only info.
    assert report.ip == "192.0.2.42" and report.rtsp_port == 8554
    assert report.preview is None  # The information page never loads thumbnails.
    ping.assert_called_once_with("192.0.2.42")
    tcp.assert_called_once_with("192.0.2.42", 8554)


class WithoutIP(FakeB2O):
    def request_json(self, *args, **kwargs):
        return {"data": {"id_camera": 12345, "camera_ip": "127.0.0.1"}}

with patch("linkvideo_vpn_helper.services.camera_diagnostics._test_tcp") as tcp:
    report_no_ip = inspect_camera(WithoutIP(), "12345", 241)
    assert not report_no_ip.ip and "не выполнялась" in report_no_ip.tcp_text
    tcp.assert_not_called()

from linkvideo_vpn_helper.services.camera_diagnostics import CameraLookupError

class Unauthorized(FakeB2O):
    token = lambda self: ""

try:
    inspect_camera(Unauthorized(), "12345", 241)
except CameraLookupError as exc:
    assert "Админка" in str(exc)
else:
    raise AssertionError("No login must block camera request")

class MissingCamera(FakeB2O):
    def request_json(self, *args, **kwargs):
        raise RuntimeError("B2O: HTTP 404 при запросе данных")

try:
    inspect_camera(MissingCamera(), "12345", 241)
except CameraLookupError as exc:
    assert "Камера не найдена" in str(exc)
else:
    raise AssertionError("404 must be actionable")

# An exact serial lookup may find a different B2O camera ID.
class SerialCamera(FakeB2O):
    def request_json(self, url, token, headers=None, timeout=15):
        if url.endswith("/cameras/89788549499"):
            raise RuntimeError("B2O: HTTP 404 при запросе данных")
        assert url.endswith("/cameras/12345")
        return {"data": {"id_camera": 12345, "serial_number": "89788549499"}}

with patch("linkvideo_vpn_helper.services.agent_b2o_preview.b2o_camera_for_serial",
           return_value=("Россия", "12345")) as exact_serial:
    serial_report = inspect_camera(SerialCamera(), "89788549499", 241, with_network=False)
    assert serial_report.camera_id == "linkvideo_12345"
    exact_serial.assert_called_once()

app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as td:
    settings = QSettings(str(Path(td) / "settings.ini"), QSettings.Format.IniFormat)
    panel = CameraDiagnosticsPanel(settings)
    assert panel.camera_id.placeholderText()
    assert not hasattr(panel, "thumbnail")  # No enormous empty preview.
    panel._on_result(0, report, "")
    assert "Серийный номер: SN123" in panel.summary.text()
    assert "Название: Вход" in panel.summary.text()
    assert "TCP:8554" in panel.summary.text()
    panel._on_result(0, None, "Камера не найдена")
    assert panel.status.text() == "Ошибка"

    b2o = B2OService(settings)
    b2o.token = lambda: "fake-token"
    def fake_request(url, token, headers=None, timeout=25):
        assert "/servers" in url
        return {"data": [{"server_name": "b2o-vcore77.video.goodline.info"}]}
    b2o.request_json = fake_request
    assert "b2o-vcore77.video.goodline.info" in b2o.dvr_servers(241, force=True)
    assert "b2o-vcore77.video.goodline.info" in b2o.cached_dvr_servers(241)
    archive = ArchiveService(settings)
    archive._remember_global_hosts(241, ["b2o-vcore88.video.goodline.info"])
    assert "b2o-vcore88.video.goodline.info" in archive._global_history_hosts(241)
    assert "b2o-vcore88.video.goodline.info" in archive._deep_candidate_hosts(
        "b2o-vcore01.video.goodline.info", 241, "linkvideo_12345"
    )

    # Discovering a camera on a newly provisioned server records its primary
    # DVR even when the requested interval has no media. No real network I/O.
    from datetime import datetime, timedelta
    from linkvideo_vpn_helper.services.archive_service_core import ArchiveCamera
    new_host = "b2o-vcore99.video.goodline.info"
    archive.b2o.camera = lambda *_: ArchiveCamera(
        "12345", "linkvideo_12345", new_host, "stream", "synthetic", 7, {}, [new_host]
    )
    archive._probe_hls_host = lambda *_: (None, [])
    archive._probe_host_fast = lambda *args, **kwargs: []
    archive.b2o.reserve_transfers = lambda *args, **kwargs: []
    archive._deep_search_missing = lambda *args, **kwargs: ([], [], 0)
    now = datetime(2026, 11, 2, 10, 0)
    archive.discover("12345", 241, now, now + timedelta(minutes=1))
    assert new_host in archive._history_hosts("linkvideo_12345")
    assert new_host in archive._global_history_hosts(241)

    # When HLS is empty, a confirmed DVR timeline interval must be accepted.
    from linkvideo_vpn_helper.services.archive_service_core import ArchiveSlice
    first = archive._local_to_epoch(now, 7)
    archive._probe_host_fast = lambda *args, **kwargs: [
        ArchiveSlice(new_host, "app", "stream", first, first + 60, "synthetic", "timeline")
    ]
    confirmed = archive.discover("12345", 241, now, now + timedelta(minutes=1))
    assert confirmed.slices and confirmed.slices[0].host == new_host
    assert confirmed.has_downloadable_archive
print("CORE TESTS 3.0.16 B2O CAMERA DIAGNOSTICS AND DVR INVENTORY OK")
