from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "installer_next/silent_update_task_windows.go").read_text(encoding="utf-8")
backend = (ROOT / "installer_next/backend_windows.go").read_text(encoding="utf-8")

# PowerShell ScheduledTasks caused real 30-60 second stalls. Provisioning/removal
# now uses native schtasks.exe under short hard deadlines.
assert '"schtasks.exe"' in source
assert '"/Create"' in source
assert '"/Delete"' in source
assert '"/RU", "SYSTEM"' in source
assert '"/RL", "HIGHEST"' in source
assert '"/SC", "ONLOGON"' in source
assert '"--scheduled"' in source
assert "4*time.Second" in source
assert "6*time.Second" in source
assert "runHiddenTimeout(" in source
assert "Register-ScheduledTask" not in source
assert "Get-ScheduledTask" not in source
assert "Unregister-ScheduledTask" not in source

# Do not synchronously query Task Scheduler a second time after successful
# /Create; Helper's runtime task_exists() is itself bounded and the privileged
# updater revalidates manifest/version/SHA before execution.
verify = source.split("func verifySilentUpdateTask", 1)[1]
assert '"schtasks.exe"' not in verify
assert "return nil" in verify

# Failure to provision optional silent patches may never abort a complete app
# install. Full Setup remains the fallback update path.
assert "recordSilentUpdateWarning(silentErr)" in backend
assert "if silentErr != nil" in backend
assert 'progress(100, "LinkVideo.Helper установлен")' in backend
assert "return appPath, nil" in backend

print("CORE TESTS 3.0.8 BOUNDED SILENT TASK OK")
