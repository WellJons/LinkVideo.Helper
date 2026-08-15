from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

service = (ROOT / "linkvideo_vpn_helper/services/silent_update_service.py").read_text(encoding="utf-8")
integration = (ROOT / "linkvideo_vpn_helper/ui/silent_update_integration.py").read_text(encoding="utf-8")
update_ux = (ROOT / "linkvideo_vpn_helper/ui/update_ux_integration.py").read_text(encoding="utf-8")
background_ux = (ROOT / "linkvideo_vpn_helper/ui/background_ux_integration.py").read_text(encoding="utf-8")
update_service = (ROOT / "linkvideo_vpn_helper/services/update_service.py").read_text(encoding="utf-8")
app = (ROOT / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
installer = (ROOT / "installer_next/silent_update_task_windows.go").read_text(encoding="utf-8")
backend = (ROOT / "installer_next/backend_windows.go").read_text(encoding="utf-8")
updater = (ROOT / "silent_updater/main_windows.go").read_text(encoding="utf-8")
trusted = (ROOT / "silent_updater/trusted_patch_windows.go").read_text(encoding="utf-8")
patcher = (ROOT / "patcher/silent_mode_windows.go").read_text(encoding="utf-8")
patcher_main = (ROOT / "patcher/main_windows.go").read_text(encoding="utf-8")
build = (ROOT / "scripts/build_next_installer.ps1").read_text(encoding="utf-8")

# Exact-version patches are the only silent path. Full Setup must still use the
# existing visible confirmation/update flow.
assert 'getattr(info, "is_patch", False)' in integration
assert "can_use_silent_patches()" in integration
assert "original_ready(self, info, error, startup)" in integration
assert "stage_patch(" in integration
assert "trigger_staged_patch()" in integration
assert "progress_callback=progress" in integration
assert "install_update_ux()" in integration
assert "install_background_ux()" in integration
assert "install_silent_patch_updates()" in app

# Manual checks own a persistent BusyDialog and real download progress instead
# of a short toast that disappears while network work is still running.
assert "BusyDialog" in update_ux
assert '"Проверяю обновления"' in update_ux
assert "progress_callback=progress" in update_ux
assert "_lv_silent_patch_progress" in update_ux
assert "_lv_silent_patch_finished" in update_ux
assert "progress_callback(0)" in update_service
assert "progress_callback(100)" in update_service

# Full Setup launch is GUI-native; runtime Python must never create cmd.exe just
# to start the installer. The global process guard also protects future FFmpeg
# and PowerShell subprocess additions.
assert '["cmd", "/c", "start"' not in update_service
assert "os.startfile" in update_service
assert "CREATE_NO_WINDOW" in background_ux
assert "subprocess.Popen = HiddenPopen" in background_ux

# The normal user process can only stage into ProgramData and trigger one fixed
# task. It never asks Task Scheduler to execute an arbitrary path.
assert 'TASK_NAME = "LinkVideo.Helper Silent Update"' in service
assert '"schtasks.exe", "/Run", "/TN", TASK_NAME' in service
assert '"pending-patch.exe"' in service
assert '"pending.json"' in service

# Setup creates the updater task once as SYSTEM/highest and grants ordinary
# users write access only to the staging folder. Uninstall removes it again.
assert "New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest" in installer
assert "Register-ScheduledTask" in installer
assert "*S-1-5-32-545:(OI)(CI)M" in installer
assert "registerSilentUpdateTask(dest)" in backend
assert "verifySilentUpdateTask(dest)" in backend
assert "removeSilentUpdateTask()" in backend

# The privileged component independently trusts only the official public
# manifest and its patch SHA, not values supplied by the unprivileged request.
assert "raw.githubusercontent.com/WellJons/LinkVideo.Helper.Updates/main/update-manifest.json" in updater
assert "findPatch(manifest.Patches, installed)" in updater
assert "sha256File(patchPath)" in updater
assert "actualHash != expectedHash" in updater
assert "prepareTrustedPatch(patchPath, expectedHash)" in updater
assert 'exec.Command(trustedPatch, "--silent")' in updater

# Close the hash-to-exec replacement window: the verified staging EXE is copied
# to Program Files and hashed again before it is executed as SYSTEM.
assert 'filepath.Join(installDir(), ".update")' in trusted
assert "sha256File(trustedPath)" in trusted
assert "!strings.EqualFold(actualHash, expectedHash)" in trusted
assert "os.O_EXCL" in trusted

# The privileged worker is also protected. It no longer runs from the generic
# TEMP directory and there is no cmd/ping/del self-cleanup process.
assert "launchProtectedWorker()" in updater
assert 'filepath.Join(installDir(), ".updater-worker")' in updater
assert "os.TempDir()" not in updater
assert 'exec.Command("cmd.exe"' not in updater
assert 'exec.Command("cmd.exe"' not in backend
assert "MoveFileExW" in backend
assert "CreationFlags: createNoWindowFlag" in backend
assert "CreationFlags: createNoWindowFlag" in patcher_main

# The patcher silent path never shows UI or starts Helper from the SYSTEM
# session. The updater executable is part of the installed payload/baseline.
assert "applyPatchSilently()" in patcher
assert "messageBox(" not in patcher
assert "launchApplication(" not in patcher
assert "explorer.exe" not in patcher
assert "LinkVideo.Helper.Updater.exe" in build
assert "silent_updater" in build

print("CORE TESTS 3.0.8 SILENT PATCH UPDATES OK")
