from __future__ import annotations
"""Regression: changing LD camera while Bitrate remains selected refreshes its data."""
import os
import time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from linkvideo_vpn_helper.ui.pages.new_agent_page import NewAgentPage
from linkvideo_vpn_helper.services.agent_service import AgentCamera, AgentService

class FakeAgent:
    def __init__(self):
        self.queries = []
    def find_camera(self, serial):
        return AgentCamera(serial_number=serial, server="agent-vcore01.video.goodline.info")
    def get_video_streams(self, camera):
        self.queries.append(camera.serial_number)
        rate = {"cameraone": "1024", "cameratwo": "2048", "camerathree": "2048"}[camera.serial_number]
        return {"VideoStreamInfos": [{"ID": 0, "VideoEncodeInfo": {"BitRate": rate}}]}
    _main_video_encode_info = staticmethod(AgentService._main_video_encode_info)

app = QApplication.instance() or QApplication([])
service = FakeAgent()
page = NewAgentPage(None, None, service=service)
page._start_preview = lambda: None
page.action_combo.setCurrentIndex(page.action_combo.findData("bitrate"))

def until(predicate):
    for _ in range(250):
        app.processEvents()
        if predicate():
            return
        time.sleep(0.008)
    raise AssertionError("Timed out reading camera bitrate")

for name, rate in (("cameraone", "1024"), ("cameratwo", "2048"), ("camerathree", "2048")):
    page.serial.setText(name)
    page._find()
    until(lambda n=name,r=rate: page.camera is not None and page.camera.serial_number==n and
          not page._busy and page.bitrate_input.text()==r)
    assert page.action_combo.currentData() == "bitrate"
    assert page.bitrate_input.isEnabled(), "Editor stays disabled after automatic reload"
    assert page.bitrate_current.text() == f"Сейчас: {rate} Кбит/с"

assert service.queries == ["cameraone", "cameratwo", "camerathree"], service.queries
page._refresh_selected_setting()
until(lambda: len(service.queries) == 4 and not page._busy)
assert page.bitrate_input.text()=="2048", "Refresh must update current camera"
print("CORE TESTS 3.0.16 AGENT CAMERA SWITCH BITRATE OK")
