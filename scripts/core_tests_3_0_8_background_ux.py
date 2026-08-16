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

assert 'dialog.setProperty("lvBackgroundWait", True)' in update_ux
assert 'dialog.property("lvBackgroundWait")' in cancel_guard
assert "Qt.WindowModality.NonModal" in cancel_guard

assert "progress_callback(0)" in update_service
assert "progress_callback(100)" in update_service
assert "os.startfile" in update_service
assert '["cmd", "/c", "start"' not in update_service

# Lifecycle and VPN-dashboard reads are automatic, deadline-bounded and use only
# daemon workers. A pathological socket must not keep the Helper process alive.
assert "setInterval(60_000)" in ux
assert "onActivated = patched_activated" in ux
assert "onDeactivated = patched_deactivated" in ux
assert "time.monotonic() + 24.0" in ux
assert "time.monotonic() + 20.0" in ux
assert "_start_daemon_batch" in ux
assert "queue.Queue" in ux
assert "threading.Semaphore" in ux
assert "daemon=True" in ux
assert "ThreadPoolExecutor(" not in ux
assert "from concurrent.futures" not in ux
assert "_lv_auto_refresh_selection" in ux
assert "_lv_auto_refresh_current" in ux

assert "_install_vpn_server_refresh_deadline" in ux
assert "self._cancel_event = cancel_event" in ux
assert "VPNServersPage.refresh = patched_refresh" in ux
assert "VPNServersPage._on_stats = patched_on_stats" in ux

assert "class HiddenPopen" in ux
assert "CREATE_NO_WINDOW" in ux
assert "subprocess.Popen = HiddenPopen" in ux

assert 'exec.Command("cmd.exe"' not in updater
assert 'exec.Command("cmd.exe"' not in backend
assert "MoveFileExW" in backend
assert "CreationFlags: createNoWindowFlag" in backend
assert "CreationFlags: createNoWindowFlag" in patcher
assert 'filepath.Join(installDir(), ".updater-worker")' in updater
assert "os.TempDir()" not in updater

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
