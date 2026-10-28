from __future__ import annotations

"""The same Setup can repair or uninstall a previous installation whose
Uninstall.exe vanished. No registry or Program Files changes in this test.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = (ROOT / "installer_next" / "backend_windows.go").read_text(encoding="utf-8")
MAIN = (ROOT / "installer_next" / "main_windows.go").read_text(encoding="utf-8")
SELFTEST = (ROOT / "installer_next" / "selftest_windows.go").read_text(encoding="utf-8")

assert 'hasArg("--uninstall")' in MAIN, "Setup must offer removal even when Uninstall.exe is missing"
assert "idRemoveInstalled" in MAIN
assert '"Удалить текущую версию"' in MAIN
assert "w.createUninstallPages()" in MAIN

register = BACKEND.split("func registerUninstall", 1)[1].split("func installProduct", 1)[0]
assert 'os.Stat(uninstall)' in register, "Must refuse broken Windows uninstall registration"
assert '"UninstallString"' in register
assert '"reg.exe", "query", uninstallKey, "/v", "UninstallString"' in register
install = BACKEND.split("func installProduct", 1)[1].split("func removeShortcuts", 1)[0]
assert install.count("verifyRuntimeSnapshot(dest)") >= 2, "Recheck files after registration"

uninstall = BACKEND.split("func uninstallProduct", 1)[1].split("func scheduleSelfDelete", 1)[0]
assert "if err := os.RemoveAll(target); err != nil" in uninstall
assert uninstall.index("os.RemoveAll(target)") < uninstall.index('runCleanup("reg.exe", "delete", key, "/f")'), (
    "Do not unregister first when the files may still be locked"
)
assert 'return fmt.Errorf("не удалось удалить %s: %w", entry.Name(), err)' in uninstall
assert 'if removeData {' in uninstall, "Keep user settings unless explicitly selected"

assert 'simulate missing uninstaller' in SELFTEST
assert 'stage repair for missing uninstaller' in SELFTEST
assert 'fresh Setup did not restore Uninstall.exe' in SELFTEST
assert "recoverInterruptedRuntimeUpgrade(dest)" in SELFTEST

print("CORE TESTS 3.0.13 UNINSTALL REPAIR AND FAIL-CLOSED CLEANUP OK")
