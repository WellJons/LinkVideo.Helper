from __future__ import annotations

"""Regression: headless bot startup task must survive Windows logoff/restart."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

from linkvideo_vpn_helper.services.telegram_service_runtime import (
    _startup_task_xml, TASK, install_startup_task, _task_command,
)

xml = _startup_task_xml(r"C:\Program Files\LinkVideo.Helper\LinkVideo.Helper.exe")
root = ET.fromstring(xml)
ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
assert root.findtext(".//t:UserId", namespaces=ns) == "S-1-5-18"
assert root.findtext(".//t:LogonType", namespaces=ns) == "ServiceAccount"
assert root.findtext(".//t:ExecutionTimeLimit", namespaces=ns) == "PT0S"
assert root.findtext(".//t:RestartOnFailure/t:Interval", namespaces=ns) == "PT1M"
assert root.findtext(".//t:MultipleInstancesPolicy", namespaces=ns) == "IgnoreNew"
assert root.findtext(".//t:BootTrigger/t:Enabled", namespaces=ns) == "true"
assert root.findtext(".//t:Arguments", namespaces=ns) == "--telegram-worker"
assert "BotFather" not in xml and "password" not in xml

with tempfile.TemporaryDirectory() as td:
    expected = str(Path(td) / "LinkVideo.Helper" / "LinkVideo.Helper.exe")
    with patch.dict(os.environ, {"ProgramFiles": td}), patch(
        "linkvideo_vpn_helper.services.telegram_service_runtime.sys.frozen",
        True, create=True,
    ), patch(
        "linkvideo_vpn_helper.services.telegram_service_runtime.sys.executable",
        expected,
    ):
        assert _task_command() == str(Path(expected).resolve())
    portable = str(Path(td) / "Downloads" / "LinkVideo.Helper.exe")
    with patch.dict(os.environ, {"ProgramFiles": td}), patch(
        "linkvideo_vpn_helper.services.telegram_service_runtime.sys.frozen",
        True, create=True,
    ), patch(
        "linkvideo_vpn_helper.services.telegram_service_runtime.sys.executable",
        portable,
    ):
        try:
            _task_command()
        except RuntimeError:
            pass
        else:
            raise AssertionError("Portable user-writable EXE was allowed for SYSTEM task")

events = []
def fake_run(argv, **kwargs):
    events.append(argv)
    if "/XML" in argv:
        payload = Path(argv[argv.index("/XML") + 1]).read_bytes()
        assert payload.startswith(b"\xff\xfe")
        text = payload.decode("utf-16")
        node = ET.fromstring(text)
        assert node.findtext(".//t:ExecutionTimeLimit", namespaces=ns) == "PT0S"
    return SimpleNamespace(returncode=0)

with patch("linkvideo_vpn_helper.services.telegram_service_runtime.os.name", "nt"), patch(
    "linkvideo_vpn_helper.services.telegram_service_runtime._task_command",
    return_value=r"C:\Program Files\LinkVideo.Helper\LinkVideo.Helper.exe",
), patch(
    "linkvideo_vpn_helper.services.telegram_service_runtime._run_hidden",
    side_effect=fake_run,
):
    install_startup_task()
assert events[0][0:2] == ["schtasks.exe", "/Create"]
assert TASK in events[0]
assert events[-1][:3] == ["schtasks.exe", "/Run", "/TN"]

print("CORE TESTS 3.0.16 TELEGRAM WINDOWS UNLIMITED BACKGROUND TASK OK")
