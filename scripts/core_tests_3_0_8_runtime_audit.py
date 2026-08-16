from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

backend = (ROOT / "installer_next/backend_windows.go").read_text(encoding="utf-8")
task = (ROOT / "installer_next/silent_update_task_windows.go").read_text(encoding="utf-8")
updater = (ROOT / "silent_updater/main_windows.go").read_text(encoding="utf-8")
patcher = (ROOT / "patcher/main_windows.go").read_text(encoding="utf-8")
service_hardening = (ROOT / "linkvideo_vpn_helper/services/runtime_hardening.py").read_text(encoding="utf-8")
archive_hardening = (ROOT / "linkvideo_vpn_helper/services/archive_process_hardening.py").read_text(encoding="utf-8")
app = (ROOT / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")

# Installer/uninstaller: no external Windows helper may have an unbounded wait.
assert '"context"' in backend
assert "context.WithTimeout" in backend
assert "exec.CommandContext" in backend
assert "defaultHelperProcessTimeout" in backend
assert "runHiddenTimeout(6*time.Second" in backend
assert "runHiddenTimeout(12*time.Second" in backend

# Real-machine freeze at 95%/18% came from PowerShell ScheduledTasks calls.
# The scheduler path is now native, bounded and optional for core installation.
assert '"schtasks.exe"' in task
assert '"/Create"' in task and '"/Query"' in task and '"/Delete"' in task
assert "Register-ScheduledTask" not in task
assert "Get-ScheduledTask" not in task
assert "Unregister-ScheduledTask" not in task
assert "runHiddenTimeout(" in task
assert "recordSilentUpdateWarning(silentErr)" in backend
assert "return appPath, nil" in backend

# SYSTEM updater and differential patcher also cannot wait forever on PowerShell,
# registry helpers or a child patch process.
assert "context.WithTimeout" in updater
assert "exec.CommandContext(patchCtx" in updater
assert "8*time.Minute" in updater
assert "os.IsNotExist(err)" in updater  # ONLOGON with no patch is a normal no-op.
assert "context.WithTimeout" in patcher
assert "exec.CommandContext" in patcher

# Automatic server selection used by create-client must not reproduce the old
# ThreadPoolExecutor context-manager hang from interactive search.
assert "FIRST_COMPLETED" in service_hardening
assert "deadline = time.monotonic()" in service_hardening
assert "pool.shutdown(wait=False, cancel_futures=True)" in service_hardening
assert "with ThreadPoolExecutor" not in service_hardening
assert "install_service_runtime_hardening()" in app

# FFmpeg HLS reads have a network-stall guard in addition to Qt-thread isolation
# and the existing Esc cancellation/progress handling.
assert '"-rw_timeout", "30000000"' in archive_hardening
assert '"-nostdin"' in archive_hardening
assert "http://" in archive_hardening and "https://" in archive_hardening
assert "install_archive_process_hardening()" in app

print("CORE TESTS 3.0.8 FULL RUNTIME FREEZE AUDIT OK")
