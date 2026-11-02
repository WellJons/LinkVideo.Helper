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
    def resolve_operator_id(self, _id, operator):
        return operator
    def operator_country(self, _operator):
        return "Россия"
    def camera(self, operator, camera_id):
        return SimpleNamespace(
            camera_id="12345", label="linkvideo_12345",
            server="b2o-vcore77.video.goodline.info",
            raw={"camera_ip": "192.0.2.42", "rtsp_port": "8554"},
        )
    def token(self):
        return "synthetic-token"

with patch("linkvideo_vpn_helper.services.camera_diagnostics._ping_windows",
           return_value="ICMP: ответ получен.") as ping, patch(
        "linkvideo_vpn_helper.services.camera_diagnostics._test_tcp",
        return_value="TCP:8554 — открыт с этого компьютера.") as tcp, patch(
        "linkvideo_vpn_helper.services.camera_diagnostics.fetch_camera_preview",
        return_value=b"not-a-real-image") as preview:
    report = inspect_camera(FakeB2O(), "12345", 241)
    assert report.camera_id == "linkvideo_12345"
    assert report.ip == "192.0.2.42" and report.rtsp_port == 8554
    assert report.preview == b"not-a-real-image"
    ping.assert_called_once_with("192.0.2.42")
    tcp.assert_called_once_with("192.0.2.42", 8554)
    assert preview.call_args.args[:2] == ("Россия", "12345")

class WithoutIP(FakeB2O):
    def camera(self, *_):
        return SimpleNamespace(camera_id="12345", label="linkvideo_12345",
            server="example.video.goodline.info",
            raw={"camera_ip": "127.0.0.1", "rtsp_port": 554})

with patch("linkvideo_vpn_helper.services.camera_diagnostics._test_tcp") as tcp:
    report = inspect_camera(WithoutIP(), "12345", 241, with_preview=False)
    assert not report.ip and "не проверялся" in report.tcp_text
    tcp.assert_not_called()

app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as td:
    settings = QSettings(str(Path(td) / "settings.ini"), QSettings.Format.IniFormat)
    panel = CameraDiagnosticsPanel(settings)
    assert panel.camera_id.placeholderText()
    panel._on_result(0, report, "")
    assert "TCP не проверялся" in panel.summary.text()

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
