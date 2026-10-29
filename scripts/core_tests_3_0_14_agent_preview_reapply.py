from __future__ import annotations

"""Offline regression: B2O Russian operator preview and repeated agent edits."""

import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from linkvideo_vpn_helper.services.agent_b2o_preview import (
    PREVIEW_OPERATORS, b2o_camera_for_serial, load_b2o_preview_for_serial,
)
from linkvideo_vpn_helper.services.agent_service import AgentCamera, AgentService
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials
from linkvideo_vpn_helper.ui.pages.new_agent_page import NewAgentPage

SERIAL = "210235UFHK3251005797"
TOKEN = "fake-no-credentials"
assert PREVIEW_OPERATORS[0] == (241, "Россия")


class FakeB2O:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def request_json(self, url, token, headers, timeout):
        assert token == TOKEN
        assert headers["Search"] == SERIAL
        assert headers["Page"] == "1"
        assert headers["Show_deleted_cameras"] == "0"
        assert timeout <= 12
        self.calls.append(url)
        operator = int(url.split("/operators/")[1].split("/")[0])
        return {"data": self.rows.get(operator, [])}


# Wrong serial from a fuzzy B2O search cannot become somebody else's picture.
fake = FakeB2O({241: [
    {"id_camera": 555, "serial_number": "ANOTHER-SERIAL"},
    {"id_camera": 777, "serial_number": SERIAL},
]})
assert b2o_camera_for_serial(SERIAL, TOKEN, fake) == ("Россия", "777")
assert len(fake.calls) == 1 and "/operators/241/cameras" in fake.calls[0]

with patch("linkvideo_vpn_helper.services.agent_b2o_preview.fetch_camera_preview",
           return_value=b"fictional-frame") as preview:
    assert load_b2o_preview_for_serial(SERIAL, TOKEN, fake) == (
        "Россия", "777", b"fictional-frame"
    )
    preview.assert_called_once_with("Россия", "777", TOKEN)

regional = FakeB2O({1741: [{"id_camera": "89", "serialNumber": SERIAL}]})
assert b2o_camera_for_serial(SERIAL, TOKEN, regional) == ("Беларусь", "89")
assert len(regional.calls) == 2
unknown = FakeB2O({241: [{"id_camera": 23, "serial_number": "WRONG"}]})
assert b2o_camera_for_serial(SERIAL, TOKEN, unknown) is None
for invalid in ("", "../password", "x" * 120):
    try:
        b2o_camera_for_serial(invalid, TOKEN, fake)
    except ValueError:
        pass
    else:
        raise AssertionError("Unsafe serial accepted")

app = QApplication.instance() or QApplication([])
creds = SessionCredentials(username="fake", password="fake")
service = AgentService(None, creds)
page = NewAgentPage(None, creds, service=service)
camera = AgentCamera(SERIAL, service.servers()[0])
page._on_ready(page._generation, "found", camera)
assert page.camera is camera
assert page.preview_label.isVisible() is False or "B2O" in page.preview_label.text()
with patch.object(page, "_load_setting"):
    page.action_combo.setCurrentIndex(page.action_combo.findData("rotation"))
    page._on_ready(page._generation, "rotation_loaded", {
        "ImageRotation": 0, "Brightness": 50,
    })
assert page.ok_btn.isEnabled()

# The confirmation/write is already complete: keep the verified current snapshot.
page._on_ready(page._generation, "done", (
    "Изменить ориентацию", "Ориентация: 0 → 3",
    "rotation", {"ImageRotation": 3, "Brightness": 50},
))
assert page.camera is camera
assert page.ok_btn.isEnabled()  # Can immediately apply a different orientation.
assert page.rotation_combo.currentData() == 3
page.rotation_combo.setCurrentIndex(page.rotation_combo.findData(1))
assert page.ok_btn.isEnabled()
assert page._image_enhance["Brightness"] == 50

with patch.object(page, "_load_setting"):
    page.action_combo.setCurrentIndex(page.action_combo.findData("bitrate"))
    page._on_ready(page._generation, "bitrate_loaded", {
        "VideoStreamInfos": [{"ID": 0, "VideoEncodeInfo": {"BitRate": "2048"}}]
    })
page._on_ready(page._generation, "done", (
    "Изменить битрейт", "Битрейт: 2048 → 4096 Кбит/с",
    "bitrate", {"VideoStreamInfos": [
        {"ID": 0, "VideoEncodeInfo": {"BitRate": "4096"}}
    ]},
))
assert page.bitrate.value() == 4096 and page.ok_btn.isEnabled()
page.close()
app.processEvents()

print("CORE TESTS AGENT PREVIEW & REAPPLY OK")
