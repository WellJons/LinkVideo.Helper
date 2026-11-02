from __future__ import annotations

"""Camera details must navigate like VPN client: in-place, not a new window."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QWidget

from linkvideo_vpn_helper.services.camera_lookup import CameraChoice
from linkvideo_vpn_helper.services.camera_live_probe import LiveProbe
from linkvideo_vpn_helper.services.camera_diagnostics import CameraDiagnostics
from linkvideo_vpn_helper.ui.camera_diagnostics_panel import CameraDiagnosticsPanel
from linkvideo_vpn_helper.ui.camera_detail_dialog import CameraDetailDialog

app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as td:
    settings = QSettings(str(Path(td) / "camera.ini"), QSettings.Format.IniFormat)
    panel = CameraDiagnosticsPanel(settings, preview_loader=lambda *args: b"")
    choices = (
        CameraChoice("139579", 241, "Россия", "Клиент Зя", "89164790064", "online"),
        CameraChoice("167024", 241, "Россия", "Нижняя Тура", "89527430240", "online"),
    )
    panel._on_result(0, choices, "")
    assert panel.view_stack.count() == 1
    assert panel.list.count() == 2

    with patch.object(CameraDetailDialog, "refresh", return_value=None):
        panel._show_camera(choices[0])
        app.processEvents()
        card = panel._active_card
        assert isinstance(card, QWidget)
        assert panel.view_stack.currentWidget() is card
        assert card.parentWidget() is panel.view_stack
        assert not card.isWindow()
        assert card.back_button.text().endswith("К результатам")
        assert card.facts is not None
        assert card.rate is not None
        assert card.detail_scroll is not None
        card._on_details(
            (0, CameraDiagnostics(
                camera_id="linkvideo_139579", server="b2o-vcore199.video.goodline.info",
                ip="", rtsp_port=0, ping_text="", tcp_text="",
                name="Клиент Зя", status="online", metadata={
                    "nimble_main_resolution": "1280x720",
                    "nimble_main_video_codec": "avc1.4d0033",
                    "nimble_main_audio_codec": "mp4a.40.2",
                },
            )), "",
        )
        probe = LiveProbe(
            "Не измерен", "Нет подтверждённой даты", "TCP:10021 — открыт",
            "RTSP: превышено время ожидания ответа.",
        )
        card._on_telemetry((0, probe))
        assert "1280x720" in card.resolution.text()
        assert "B2O" in card.resolution.text()
        assert "Не измерен" in card.fps.text()
        assert "RTSP:" in card.diagnostic_text.text()
        card.back_button.click()
        app.processEvents()
    assert panel._active_card is None
    assert panel.view_stack.currentWidget() is panel.search_view
    assert panel.list.count() == 2
    panel.close()

print("CORE TESTS 3.0.16 CAMERA IN-PLACE NAVIGATION AND REFERENCE DATA OK")
