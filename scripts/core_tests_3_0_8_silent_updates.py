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

assert 'getattr(info, "is_patch", False)' in integration
assert "can_use_silent_patches()" in integration
assert "original_ready(self, info, error, startup)" in integration
assert "stage_patch(" in integration
assert "trigger_staged_patch()" in integration
assert "progress_callback=progress" in integration
assert "install_update_ux()" in integration
assert "install_background_ux()" in integration
assert "install_silent_patch_updates()" in app

assert "BusyDialog" in update_ux
assert '"Проверяю обновления"' in update_ux
assert "progress_callback=progress" in update_ux
assert "_lv_silent_patch_progress" in update_ux
assert "_lv_silent_patch_finished" in update_ux
assert "progress_callback(0)" in update_service
assert "progress_callback(100)" in update_service

assert '["cmd", "/c", "start"' not in update_service
assert "os.startfile" in update_service
assert "CREATE_NO_WINDOW" in background_ux
assert "subprocess.Popen = HiddenPopen" in background_ux

assert 'TASK_NAME = "LinkVideo.Helper Silent Update"' in service
assert '"schtasks.exe", "/Run", "/TN", TASK_NAME' in service
assert '"pending-patch.exe"' in service
assert '"pending.json"' in service

# Setup creates one fixed SYSTEM/highest task with the native Task Scheduler CLI,
# and does not import the PowerShell ScheduledTasks module. All calls are bounded.
assert '"schtasks.exe"' in installer
assert '"/Create"' in installer
assert '"/RU", "SYSTEM"' in installer
assert '"/RL", "HIGHEST"' in installer
assert "runHiddenTimeout(" in installer
assert "Register-ScheduledTask" not in installer
assert "Get-ScheduledTask" not in installer
assert "Unregister-ScheduledTask" not in installer
assert "*S-1-5-32-545:(OI)(CI)M" in installer
assert "registerSilentUpdateTask(dest)" in backend
assert "verifySilentUpdateTask(dest)" in backend
assert "removeSilentUpdateTask()" in backend

assert "raw.githubusercontent.com/WellJons/LinkVideo.Helper.Updates/main/update-manifest.json" in updater
assert "findPatch(manifest.Patches, installed)" in updater
assert "sha256File(patchPath)" in updater
assert "actualHash != expectedHash" in updater
assert "prepareTrustedPatch(patchPath, expectedHash)" in updater
assert 'exec.CommandContext(patchCtx, trustedPatch, "--silent")' in updater
assert "context.WithTimeout" in updater
assert "os.IsNotExist(err)" in updater

assert 'filepath.Join(installDir(), ".update")' in trusted
assert "sha256File(trustedPath)" in trusted
assert "!strings.EqualFold(actualHash, expectedHash)" in trusted
assert "os.O_EXCL" in trusted

assert "launchProtectedWorker()" in updater
assert 'filepath.Join(installDir(), ".updater-worker")' in updater
assert "os.TempDir()" not in updater
assert 'exec.Command("cmd.exe"' not in updater
assert 'exec.Command("cmd.exe"' not in backend
assert "MoveFileExW" in backend
assert "CreationFlags: createNoWindowFlag" in backend
assert "CreationFlags: createNoWindowFlag" in patcher_main
assert "exec.CommandContext" in patcher_main

# Never pass a filesystem path after PowerShell -Command. That exact launch
# shape broke the 2.0.2 updater when a path with spaces/non-ASCII text was
# parsed as PowerShell syntax instead of data.
for name, source in (("patcher", patcher_main), ("silent updater", updater)):
    assert "LINKVIDEO_PRODUCT_VERSION_FILE" in source, name
    assert '"-Command", script, path' not in source, name
    assert "cmd.Env = append(os.Environ(), productVersionPathEnvKey+\"=\"+path)" in source, name

assert "applyPatchSilently()" in patcher
assert "messageBox(" not in patcher
assert "launchApplication(" not in patcher
assert "explorer.exe" not in patcher
assert 'parameters, _ = syscall.UTF16PtrFromString("--silent")' in patcher_main
assert "LpParameters: parameters" in patcher_main
assert "errElevationDelegated" in patcher_main
assert "rollbackAfterFailure(" in patcher_main
assert "rollbackAfterFailure(" in patcher

# Never advertise the target DisplayVersion before ProductVersion validation.
for name, source in (("interactive patcher", patcher_main), ("silent patcher", patcher)):
    version_probe = source.index("nextVersion")
    registry_write = source.index('"DisplayVersion"', version_probe)
    assert registry_write > version_probe, name
    assert 'if err := runHidden(' in source[version_probe:registry_write + 500], name
assert "LinkVideo.Helper.Updater.exe" in build
assert "silent_updater" in build

print("CORE TESTS 3.0.8 SILENT PATCH UPDATES OK")
