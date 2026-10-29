from __future__ import annotations

"""Offline camera agent contract and themed UI smoke tests."""

import json
import os
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QComboBox
from linkvideo_vpn_helper.services.agent_service import AgentCamera, AgentService
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials
from linkvideo_vpn_helper.ui.pages.new_agent_page import NewAgentPage
from linkvideo_vpn_helper.ui.main_window import MainWindow
from linkvideo_vpn_helper.ui.nav_icons import navigation_icon

creds = SessionCredentials(username="fictional-operator", password="not-real")
service = AgentService(None, creds)
assert service.PORT == 19999
assert len(service.servers()) == 10
assert MainWindow.NAV_ITEMS.index(next(x for x in MainWindow.NAV_ITEMS if x[0] == "new_agent")) == (
    MainWindow.NAV_ITEMS.index(next(x for x in MainWindow.NAV_ITEMS if x[0] == "tariffs")) + 1
)

class FakeResponse:
    status_code = 200
    def __init__(self, payload):
        self.content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    def raise_for_status(self):
        pass
    def json(self):
        return json.loads(self.content)

calls = []
image_settings = {"ImageRotation": 0, "Brightness": 50}
video_settings = {
    "VideoStreamInfos": [
        {"ID": 0, "VideoEncodeInfo": {"BitRate": "1024", "Width": 1920}},
        {"ID": 1, "VideoEncodeInfo": {"BitRate": "256"}},
    ]
}
serial = "210235UFHK3251001212"
host = "agent-vcore01.video.goodline.info"

def fake_get(url, **kwargs):
    calls.append(("GET", url, kwargs))
    assert kwargs["auth"] == ("fictional-operator", "not-real")
    assert kwargs["allow_redirects"] is False
    if url.endswith("/camera/list"):
        if url.startswith(f"http://{host}:19999/"):
            return FakeResponse([{
                "serial_number": serial, "agent_version": [1, 2, 3],
                "vendor": "LD", "model": "demo", "connect_time": "today",
            }])
        return FakeResponse([])
    raise AssertionError(url)

def fake_put(url, **kwargs):
    calls.append(("PUT", url, kwargs))
    assert kwargs["allow_redirects"] is False
    assert kwargs["auth"] == ("fictional-operator", "not-real")
    raw = kwargs["data"]
    if raw.startswith(b"GET "):
        path = raw.split(b" ", 2)[1].decode()
        if path == service.IMAGE_ENHANCE_PATH:
            return FakeResponse({"Response": {"ResponseCode": 0, "Data": dict(image_settings)}})
        if path == service.VIDEO_STREAMS_PATH:
            return FakeResponse({"Response": {"ResponseCode": 0, "Data": video_settings}})
        raise AssertionError(path)
    if raw.startswith(b"PUT "):
        path = raw.split(b" ", 2)[1].decode()
        body = json.loads(raw.split(b"\r\n\r\n", 1)[1])
        assert f"Content-Length: {len(raw.split(b'\r\n\r\n', 1)[1])}".encode() in raw
        if path == service.IMAGE_ENHANCE_PATH:
            image_settings.update(body)
        elif path == service.VIDEO_STREAMS_PATH:
            video_settings.update(body)
        elif path.startswith("/LAPI/V1.0/System/Time"):
            pass
        else:
            raise AssertionError(path)
        return FakeResponse({"Response": {"ResponseCode": 0}})
    raise AssertionError(raw[:20])

with patch("linkvideo_vpn_helper.services.agent_service.requests.get", side_effect=fake_get), \
     patch("linkvideo_vpn_helper.services.agent_service.requests.put", side_effect=fake_put):
    camera = service.find_camera(serial)
    assert isinstance(camera, AgentCamera)
    assert camera.server == host
    assert camera.version_text == "1.2.3"
    assert service.find_camera(serial) == camera
    before, after = service.set_image_rotation(camera, service.get_image_enhance(camera), 3)
    assert (before, after) == (0, 3)
    before, after = service.set_bitrate(camera, service.get_video_streams(camera), 2048)
    assert (before, after) == ("1024", "2048")
    assert video_settings["VideoStreamInfos"][1]["VideoEncodeInfo"]["BitRate"] == "256"
    assert len(service.configure_time(camera, 7)) == 3
    for bad in ("", "../password", "bad/slash", "a" * 150):
        try:
            service.find_camera(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe serial accepted")
    try:
        service.proxy_pipe(AgentCamera(serial, "evil.example"), b"GET /")
    except ValueError:
        pass
    else:
        raise AssertionError("External host accepted")


# A camera beyond the first (truncated) inventory page must still be found.
late_serial = "210235UFHK3251005463"
paging_calls = []
def paginated_get(url, **kwargs):
    paging_calls.append((url, kwargs.get("params")))
    if not url.startswith(f"http://{host}:19999/"):
        return FakeResponse([])
    params = kwargs.get("params")
    if params is None:
        return FakeResponse({"items": [{"serial_number": serial}], "total": 2})
    assert params == {"offset": 1, "limit": 1}
    return FakeResponse({"items": [{"serial_number": late_serial, "vendor": "LD"}], "total": 2})

with patch("linkvideo_vpn_helper.services.agent_service.requests.get", side_effect=paginated_get):
    paged = AgentService(None, creds)
    found_late = paged.find_camera(late_serial)
    assert found_late.serial_number == late_serial and found_late.server == host
    assert any(params == {"offset": 1, "limit": 1} for _, params in paging_calls)

# Parsing a nested camera list and a noncanonical serial key is supported.
assert AgentService._camera_rows({"data": {"cameras": [{"serialNumber": late_serial}]}}) == [
    {"serialNumber": late_serial}
]

# Unrecognized responses on any server are not falsely shown as "not found".
def incomplete_get(url, **kwargs):
    if url.startswith(f"http://{host}:19999/"):
        return FakeResponse({"unexpected": {"error": "inventory not provided"}})
    return FakeResponse([])

with patch("linkvideo_vpn_helper.services.agent_service.requests.get", side_effect=incomplete_get):
    try:
        AgentService(None, creds).find_camera("210235UFHK3251009999")
    except ConnectionError as exc:
        assert "Поиск не завершён" in str(exc)
        assert host in str(exc)
    else:
        raise AssertionError("Partial inventory incorrectly counted as not found")

app = QApplication.instance() or QApplication([])
assert not navigation_icon("new_agent").isNull()

page = NewAgentPage(None, creds, service=service)
assert page.findChild(QPushButton, "NewAgentFind") is not None
assert page.findChild(QComboBox, "NewAgentAction") is not None
assert page.camera is None and page.actions_card.isHidden()
page._on_ready(page._generation, "found", camera)
assert page.camera is camera
assert page.camera_card.isHidden() is False
assert page.result_summary.isHidden() is False
assert page.copy_btn.isEnabled()
assert serial in page.info.text()
page.action_combo.setCurrentIndex(page.action_combo.findData("time"))
assert page.ok_btn.isEnabled()
# Denied confirmation never sends a settings write.
with patch("linkvideo_vpn_helper.ui.pages.new_agent_page.ConfirmDialog.exec",
           return_value=QDialog.DialogCode.Rejected):
    page._execute_selected()
assert not page._busy
# Suppress the asynchronous real API read in UI smoke tests.
with patch.object(page, "_load_setting") as load:
    page.action_combo.setCurrentIndex(page.action_combo.findData("rotation"))
    load.assert_called_once_with("rotation")
    page._on_ready(page._generation, "rotation_loaded", {"ImageRotation": 3})
    assert page.ok_btn.isEnabled()
    page.action_combo.setCurrentIndex(page.action_combo.findData("bitrate"))
    page._on_ready(page._generation, "bitrate_loaded", video_settings)
assert page.bitrate.value() == 2048
page.close()
app.processEvents()

print("CORE TESTS NEW AGENT LD SETTINGS & UI OK")
