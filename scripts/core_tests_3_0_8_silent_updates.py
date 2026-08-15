from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

service = (ROOT / "linkvideo_vpn_helper/services/silent_update_service.py").read_text(encoding="utf-8")
integration = (ROOT / "linkvideo_vpn_helper/ui/silent_update_integration.py").read_text(encoding="utf-8")
app = (ROOT / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
installer = (ROOT / "installer_next/silent_update_task_windows.go").read_text(encoding="utf-8")
backend = (ROOT / "installer_next/backend_windows.go").read_text(encoding="utf-8")
updater = (ROOT / "silent_updater/main_windows.go").read_text(encoding="utf-8")
trusted = (ROOT / "silent_updater/trusted_patch_windows.go").read_text(encoding="utf-8")
patcher = (ROOT / "patcher/silent_mode_windows.go").read_text(encoding="utf-8")
build = (ROOT / "scripts/build_next_installer.ps1").read_text(encoding="utf-8")

# Exact-version patches are the only silent path. Full Setup must still use the
# existing visible confirmation/update flow.
assert 'getattr(info, "is_patch", False)' in integration
assert "can_use_silent_patches()" in integration
assert "original_ready(self, info, error, startup)" in integration
assert "stage_patch(" in integration
assert "trigger_staged_patch()" in integration
assert "install_silent_patch_updates()" in app

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

# The updater runs its worker from TEMP so a patch may replace the installed
# updater itself. The patcher silent path never shows UI or starts Helper from
# the SYSTEM session.
assert "launchTempWorker()" in updater
assert '"--scheduled-worker"' in updater
assert "applyPatchSilently()" in patcher
assert "messageBox(" not in patcher
assert "launchApplication(" not in patcher
assert "explorer.exe" not in patcher

# The updater executable is part of the normal installed payload/baseline.
assert "LinkVideo.Helper.Updater.exe" in build
assert "silent_updater" in build

print("CORE TESTS 3.0.8 SILENT PATCH UPDATES OK")
