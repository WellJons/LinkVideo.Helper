from __future__ import annotations

"""Regression: opening a camera must start loading and populate all panels.

This test deliberately DOES NOT mock CameraDetailDialog.refresh: previous UI
tests mocked it and missed a deleted-widget AttributeError which left every
field stuck on "Проверяю…" before a worker was ever started.
"""
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from linkvideo_vpn_helper.services.camera_diagnostics import CameraDiagnostics
from linkvideo_vpn_helper.services.camera_live_probe import LiveProbe
from linkvideo_vpn_helper.services.camera_lookup import CameraChoice
from linkvideo_vpn_helper.ui.camera_detail_dialog import CameraDetailDialog

app = QApplication.instance() or QApplication([])
calls = {"inspector": 0, "preview": 0, "probe": 0, "stream": 0}

def inspector(_b2o, camera_id, operator_id, **kwargs):
    calls["inspector"] += 1
    assert camera_id == "139579" and operator_id == 241
    assert kwargs["with_network"] is False
    return CameraDiagnostics(
        "linkvideo_139579", "b2o-vcore199.video.goodline.info", "",
        0, "", "", name="Клиент Зя", status="online",
        metadata={
            "nimble_main_resolution": "1280x720",
            "nimble_main_video_codec": "avc1.4d0033",
            "nimble_main_audio_codec": "mp4a.40.2",
            "owner_login": "89164790064",
        },
    )

def preview_loader(*args):
    calls["preview"] += 1
    return b""  # expected preview-unavailable path

def live_probe(*args):
    calls["probe"] += 1
    return LiveProbe(
        "≈1.24 Мбит/с", "Поток получен", "TCP:10021 — открыт",
        "Четырёхсекундная проверка видеопакетов завершена.",
        video_codec="h264", audio_codec="aac",
        resolution="1280x720", fps="24.9", source="RTSP",
    )

def stream_reader(*args):
    calls["stream"] += 1
    return None, "Тест: ссылка на RTSP недоступна."

with tempfile.TemporaryDirectory() as td:
    settings = QSettings(str(Path(td) / "regression.ini"), QSettings.Format.IniFormat)
    choice = CameraChoice("139579", 241, "Россия", "Клиент Зя",
                          "89164790064", "online")
    with patch(
        "linkvideo_vpn_helper.ui.camera_detail_dialog.fetch_camera_stream_with_status",
        side_effect=stream_reader,
    ):
        card = CameraDetailDialog(
            choice, settings, inspector=inspector,
            preview_loader=preview_loader, live_probe=live_probe,
        )
        card.show()
        # Execute the actual QTimer.singleShot(0, refresh), without calling
        # refresh ourselves and without any real B2O or stream access.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            app.processEvents()
            if (calls["inspector"] and calls["preview"] and
                    calls["probe"] and card.rate.text() == "≈1.24 Мбит/с"):
                break
            time.sleep(0.01)
        else:
            raise AssertionError(
                "Camera startup stalled; worker or UI did not complete: "
                + str(calls) + ", status=" + card.status.text()
            )
        assert card.refresh_btn.isEnabled()
        assert "онлайн" in card.status.text().lower()
        assert card.heading.text() == "Клиент Зя"
        assert card.resolution.text() == "1280x720"
        assert card.fps.text() == "24.9"
        assert card.video_codec.text() == "H.264"
        assert card.audio_codec.text() == "AAC"
        assert "Тест: ссылка" in card.diagnostic_text.text()
        # Fail-open network/metadata failures must exit the loading state.
        card.refresh()
        card._on_details((card._generation, None), "Контрольная ошибка.")
        assert card.refresh_btn.isEnabled()
        assert card.status.text() == "Обновление не удалось"
        card.deactivate()
        card.close()

print("CORE TESTS 3.0.16 CAMERA ACTUAL AUTO-REFRESH STARTUP OK")
