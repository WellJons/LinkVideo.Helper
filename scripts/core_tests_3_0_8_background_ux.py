from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ux = (ROOT / "linkvideo_vpn_helper/ui/background_ux_integration.py").read_text(encoding="utf-8")
update_ux = (ROOT / "linkvideo_vpn_helper/ui/update_ux_integration.py").read_text(encoding="utf-8")
update_service = (ROOT / "linkvideo_vpn_helper/services/update_service.py").read_text(encoding="utf-8")
silent = (ROOT / "linkvideo_vpn_helper/ui/silent_update_integration.py").read_text(encoding="utf-8")
cancel_guard = (ROOT / "linkvideo_vpn_helper/ui/operation_cancel_guard.py").read_text(encoding="utf-8")
updater = (ROOT / "silent_updater/main_windows.go").read_text(encoding="utf-8")
backend = (ROOT / "installer_next/backend_windows.go").read_text(encoding="utf-8")
patcher = (ROOT / "patcher/main_windows.go").read_text(encoding="utf-8")

# Manual update UX must stay visible for the real duration of the operation and
# receive worker progress through Qt signals, not touch widgets from a thread.
assert "class _UpdateBridge(QObject)" in update_ux
assert "progress = Signal(int, str)" in update_ux
assert "BusyDialog" in update_ux
assert '"Проверяю обновления"' in update_ux
assert '"Скачиваю патч"' in update_ux
assert '"Скачиваю обновление"' in update_ux
assert "progress_callback=progress" in update_ux
assert "bridge.progress.emit" in update_ux
assert "_lv_silent_patch_progress" in update_ux
assert "_lv_silent_patch_finished" in update_ux
assert "progress_callback=progress" in silent

# The visible update indicator is informational, not a trap: the operator may
# still move/close Helper while the bounded request finishes in the background.
assert 'dialog.setProperty("lvBackgroundWait", True)' in update_ux
assert 'dialog.property("lvBackgroundWait")' in cancel_guard
assert "Qt.WindowModality.NonModal" in cancel_guard

# UpdateService exposes determinate progress and no longer creates an
# intermediate cmd window to start Setup.
assert "progress_callback(0)" in update_service
assert "progress_callback(100)" in update_service
assert "os.startfile" in update_service
assert '["cmd", "/c", "start"' not in update_service

# Dynamic lifecycle data refreshes automatically while the page is active. The
# worker has a hard wall-clock deadline and never waits on a stuck executor.
assert "setInterval(60_000)" in ux
assert "onActivated = patched_activated" in ux
assert "onDeactivated = patched_deactivated" in ux
assert "time.monotonic() + 24.0" in ux
assert "FIRST_COMPLETED" in ux
assert "pool.shutdown(wait=False, cancel_futures=True)" in ux
assert "_lv_auto_refresh_selection" in ux
assert "_lv_auto_refresh_current" in ux

# The VPN dashboard already refreshes itself every 20 seconds in its page code;
# the integration adds a deadline and real Esc cancellation to that existing
# refresh path instead of letting one dead server block the whole dashboard.
assert "_install_vpn_server_refresh_deadline" in ux
assert "time.monotonic() + 20.0" in ux
assert "self._cancel_event = cancel_event" in ux
assert "VPNServersPage.refresh = patched_refresh" in ux
assert "VPNServersPage._on_stats = patched_on_stats" in ux

# Runtime subprocess guard covers FFmpeg and any future Python console helpers.
assert "class HiddenPopen" in ux
assert "CREATE_NO_WINDOW" in ux
assert "subprocess.Popen = HiddenPopen" in ux

# Privileged/install-time helpers also avoid cmd.exe and console creation.
assert 'exec.Command("cmd.exe"' not in updater
assert 'exec.Command("cmd.exe"' not in backend
assert "MoveFileExW" in backend
assert "CreationFlags: createNoWindowFlag" in backend
assert "CreationFlags: createNoWindowFlag" in patcher
assert 'filepath.Join(installDir(), ".updater-worker")' in updater
assert "os.TempDir()" not in updater

# Guard the whole runtime tree, not only the files touched in this PR. Build
# scripts may use cmd by design, but the installed GUI/updater runtime may not
# spawn a console shell as an implementation detail.
runtime_roots = [
    ROOT / "linkvideo_vpn_helper",
    ROOT / "installer_next",
    ROOT / "patcher",
    ROOT / "silent_updater",
]
for runtime_root in runtime_roots:
    for path in runtime_root.rglob("*"):
        if path.suffix.lower() not in {".py", ".go"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "os.system(" not in text, f"console shell API in runtime: {path.relative_to(ROOT)}"
        assert 'exec.Command("cmd.exe"' not in text, f"cmd.exe in runtime: {path.relative_to(ROOT)}"
        assert 'exec.Command("cmd"' not in text, f"cmd in runtime: {path.relative_to(ROOT)}"
        assert '["cmd", "/c"' not in text.lower(), f"cmd /c in runtime: {path.relative_to(ROOT)}"
        assert '["cmd.exe", "/c"' not in text.lower(), f"cmd.exe /c in runtime: {path.relative_to(ROOT)}"

print("CORE TESTS 3.0.8 BACKGROUND UX / NO-CONSOLE OK")
