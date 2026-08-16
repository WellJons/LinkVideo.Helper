from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "installer_next/silent_update_task_windows.go").read_text(encoding="utf-8")
backend = (ROOT / "installer_next/backend_windows.go").read_text(encoding="utf-8")

# ScheduledTasks PowerShell cmdlets caused real 30-60 second stalls on a Windows
# workstation. Provisioning/removal now uses the native scheduler CLI and every
# invocation has a hard deadline.
assert '"schtasks.exe"' in source
assert '"/Create"' in source
assert '"/Query"' in source
assert '"/Delete"' in source
assert '"/RU", "SYSTEM"' in source
assert '"/RL", "HIGHEST"' in source
assert '"/SC", "ONLOGON"' in source
assert '"--scheduled"' in source
assert "runHiddenTimeout(" in source
assert "Register-ScheduledTask" not in source
assert "Get-ScheduledTask" not in source
assert "Unregister-ScheduledTask" not in source

# Failure to provision the optional silent-patch convenience may never roll back
# a complete Helper installation. The normal visible Setup path remains usable.
assert "recordSilentUpdateWarning(silentErr)" in backend
assert "if silentErr != nil" in backend
assert 'progress(100, "LinkVideo.Helper установлен")' in backend
assert "return appPath, nil" in backend

print("CORE TESTS 3.0.8 BOUNDED SILENT TASK OK")
