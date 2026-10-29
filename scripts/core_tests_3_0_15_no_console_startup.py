from __future__ import annotations

"""Prevent Windows command-prompt flashes during Helper startup.

This test uses a fake process constructor: no console child is actually
started and no network or external commands are invoked.
"""

import copy
import os
from pathlib import Path
import subprocess

from linkvideo_vpn_helper.ui import background_ux_integration as background

root = Path(__file__).resolve().parents[1]
source = (root / "linkvideo_vpn_helper" / "app.py").read_text(encoding="utf-8")
assert source.index("_install_no_console_process_guard()") < source.index("app = QApplication(sys.argv)")
assert source.count("_install_no_console_process_guard()") == 1

if os.name == "nt":
    original = subprocess.Popen
    calls = []

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            calls.append(self)

    subprocess.Popen = FakePopen
    try:
        background._install_no_console_process_guard()
        wrapped = subprocess.Popen
        assert wrapped is not FakePopen
        assert getattr(wrapped, "_lv_no_console_guard", False)
        background._install_no_console_process_guard()
        assert subprocess.Popen is wrapped, "Repeated guard installation should be idempotent"

        visible = subprocess.Popen(
            ["cmd.exe", "/c", "exit", "fake-secret-argument"],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        assert visible.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert not (visible.kwargs["creationflags"] & subprocess.CREATE_NEW_CONSOLE)
        assert visible.kwargs["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW
        assert visible.kwargs["startupinfo"].wShowWindow == 0

        previous = subprocess.STARTUPINFO()
        previous.dwFlags = 0
        previous.wShowWindow = 5
        regular = subprocess.Popen(["ffprobe.exe", "-version"], startupinfo=previous)
        assert regular.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert regular.kwargs["startupinfo"].wShowWindow == 0
        assert previous.wShowWindow == 5, "Do not mutate a caller-owned STARTUPINFO object"

        detached = subprocess.Popen(
            ["helper.exe"], creationflags=subprocess.DETACHED_PROCESS
        )
        assert detached.kwargs["creationflags"] & subprocess.DETACHED_PROCESS
        assert not detached.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert all(call.kwargs["startupinfo"].wShowWindow == 0 for call in calls)

        assert all("fake-secret-argument" not in str(call.kwargs)
                   for call in calls), "No secret arguments belong to subprocess kwargs"
    finally:
        subprocess.Popen = original

print("CORE TESTS WINDOWS STARTUP NO-CONSOLE GUARD OK")
