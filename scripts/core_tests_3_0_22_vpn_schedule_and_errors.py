from __future__ import annotations

"""Windows-safe task creation regression, with no real administrator actions.

Mimics DPAPI-unsealed configuration and task APIs without ever making live
changes to Task Scheduler, ProgramData or RouterOS.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from linkvideo_vpn_helper.services import vpn_background_service as bg


def try_install(interval: int, *, fail_at: str = ""):
    with TemporaryDirectory() as root:
        directory = Path(root)
        temp = directory / "pending-123.json"
        temp.write_text('{"sealed":"opaque"}', encoding="utf-8")
        target = directory / "config.json"
        target.write_text("OLD_ENCRYPTED_CONFIG", encoding="utf-8")
        commands = []

        def run(command, **_):
            commands.append(command)
            if fail_at and fail_at in command:
                raise OSError(5, "Access denied by Task Scheduler")
            return None

        with patch.object(bg, "_is_elevated", return_value=True), \
             patch.object(bg, "_unpack_settings", return_value={"interval_hours": interval}), \
             patch.object(bg, "_data_dir", return_value=directory), \
             patch.object(bg, "_secure_directory"), \
             patch.object(bg, "_installed_executable", return_value=Path(r"C:\Program Files\LinkVideo.Helper\LinkVideo.Helper.exe")), \
             patch.object(bg, "_run_command", side_effect=run):
            result = bg._admin_install(temp)

        task = next((command for command in commands
                     if command[0].lower() == "schtasks.exe" and "/Create" in command), None)
        assert task is not None
        assert task[task.index("/TR") + 1].endswith('--vpn-background-worker')
        assert all("password" not in str(part).lower() for part in task)
        if fail_at == "/Create":
            assert result == 14, result
            assert target.read_text(encoding="utf-8") == "OLD_ENCRYPTED_CONFIG"
        else:
            assert result == 0, result
            assert not temp.exists()
        return task


for hours, expected in [
    (6, ["/SC", "HOURLY", "/MO", "6"]),
    (12, ["/SC", "HOURLY", "/MO", "12"]),
    (24, ["/SC", "DAILY", "/MO", "1"]),
]:
    task = try_install(hours)
    assert all(value in task for value in expected), task

try_install(12, fail_at="/Create")

for code, wording in [(10, "конфигурац"), (11, "DPAPI"),
                      (12, "ProgramData"), (14, "Планировщик")]:
    assert wording.lower() in bg.INSTALL_ERROR_MESSAGES[code].lower()

print("CORE TESTS 3.0.22 VPN TASK INTERVALS AND DIAGNOSTIC FAILURES OK")
