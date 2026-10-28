from __future__ import annotations

"""Safety regression: the supported update path is visible full Setup only.

Legacy patch binaries stay in the repository for old-version compatibility,
but new desktop startup and new installation must not activate them.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
app = (ROOT / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
integration = (ROOT / "linkvideo_vpn_helper/ui/silent_update_integration.py").read_text(encoding="utf-8")
update_ui = (ROOT / "linkvideo_vpn_helper/ui/update_ux_integration.py").read_text(encoding="utf-8")
update_service = (ROOT / "linkvideo_vpn_helper/services/update_service.py").read_text(encoding="utf-8")
installer = (ROOT / "installer_next/backend_windows.go").read_text(encoding="utf-8")
build = (ROOT / "scripts/build_next_installer.ps1").read_text(encoding="utf-8")

assert "install_silent_patch_updates()" not in app
assert "install_update_ux()" in app
assert "stage_patch(" not in integration
assert "trigger_staged_patch(" not in integration
assert "closeEvent" not in integration
assert "install_update_ux()" in integration  # old entrypoint stays import-compatible

assert "can_use_silent_patches" not in update_ui
assert "silent_ready" not in update_ui
assert "artifact_kind=\"setup\"" in update_ui
assert '"Проверяю обновления"' in update_ui
assert '"Скачиваю обновление"' in update_ui
assert "progress_callback=progress" in update_ui
assert "bridge.progress.emit" in update_ui

assert 'manifest["patches"]' in update_service  # compatibility note, not selection
assert "patches = data.get" not in update_service
assert "os.startfile" in update_service
assert "progress_callback(0)" in update_service
assert "progress_callback(100)" in update_service

install_body = installer.split("func installProduct(", 1)[1].split("func removeShortcuts", 1)[0]
assert "removeSilentUpdateTask()" in install_body
assert "registerSilentUpdateTask(" not in install_body
assert "verifySilentUpdateTask(" not in install_body
assert "recordSilentUpdateWarning(nil)" in install_body

# Keep legacy patch builder files out of scope of the live UI/installer migration.
assert "LinkVideo.Helper_Setup.exe" in build

print("CORE TESTS: VISIBLE FULL SETUP UPDATES ONLY OK")
