from __future__ import annotations

"""Reproduce a Scheduler task invisible to an ordinary Windows user.

The old 3.0.22 GUI falsely reported failure after an elevated /Create + /Run
succeeded, because the unelevated /Query returned Access Denied / not found.
No real tasks are created or deleted by this test.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import subprocess

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from linkvideo_vpn_helper.services import vpn_background_service as bg
from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.ui.pages.settings_page import SettingsPage

task = bg.TASK_NAME
receipt = {"installed": True, "interval_hours": 12}
access_denied = subprocess.CompletedProcess(
    ["schtasks.exe", "/Query", "/TN", task], 5, "", "Access denied"
)

with patch.object(bg, "_on_windows", return_value=True), \
     patch.object(bg, "_run_command", return_value=access_denied), \
     patch.object(bg, "read_task_receipt", return_value=receipt):
    inspected = bg.task_inspection()
    assert inspected["installed"] is True
    assert inspected["verified"] is False
    assert inspected["source"] == "admin_receipt"
    assert bg.task_registered() is True

# A 0 exit code from the elevated process is authoritative; do not query
# the hidden task again from a less privileged desktop login.
with patch.object(bg, "_elevate", return_value=0), \
     patch.object(bg, "task_registered", return_value=False):
    assert bg.enable_task(Path(r"C:\temp\pending-123.json")) is None

calls = []
def cmd(command, **kwargs):
    calls.append(command)
    return subprocess.CompletedProcess(command, 0, "", "")
with patch.object(bg, "_is_elevated", return_value=True), \
     patch.object(bg, "_run_command", side_effect=cmd), \
     patch.object(bg, "_config_path", return_value=Path("unreadable-or-absent")), \
     patch.object(bg, "_write_task_receipt") as write_receipt:
    assert bg._admin_verify() == 0
    write_receipt.assert_called_once_with(installed=True, interval_hours=12)
assert any("/Query" in cmd for cmd in calls)

with patch.object(bg, "_elevate", return_value=0), \
     patch.object(bg, "read_task_receipt", return_value=receipt):
    assert bg.verify_task_as_administrator() is None

# Even when the ProgramData credential directory is unreadable, the
# ordinary desktop operator can view the non-secret backup health summary.
with patch.object(bg, "_read_public_status", return_value={
    "checked_utc": "2026-11-09 12:00:00Z", "state": "ok",
    "servers": 12, "successful": 12, "failed": 0,
}), patch.object(bg, "_status_file", side_effect=PermissionError("No access")):
    status = bg.read_status()
    assert status["successful"] == 12
    assert status["state"] == "ok"

app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as td:
    settings = QSettings(str(Path(td) / "prefs.ini"), QSettings.Format.IniFormat)
    registry = ServerRegistry(settings)
    with patch.object(bg, "task_registered", return_value=True), \
         patch.object(bg, "task_inspection", return_value={
             "installed": True, "verified": False, "source": "admin_receipt",
         }), patch.object(bg, "read_task_receipt", return_value=receipt), \
         patch.object(bg, "read_status", return_value={
             "state": "ok", "checked_utc": "2026-11-09 12:00:00Z",
             "successful": 12, "failed": 0,
         }):
        page = SettingsPage(settings, registry)
        assert page.bg_enabled.isChecked() is True
        assert "Регистрация подтверждена администратором" in page.bg_status.text()
        assert "12" in page.bg_status.text()
        assert page.bg_admin_check.isVisible() is False or page.bg_admin_check.isHidden() is False
        page.close()

# No RouterOS passwords, Google keys or token fields in the readable receipt.
assert set(receipt) == {"installed", "interval_hours"}

print("CORE TESTS: NONADMIN SCHEDULER ACCESS FALSE NEGATIVE FIX OK")
