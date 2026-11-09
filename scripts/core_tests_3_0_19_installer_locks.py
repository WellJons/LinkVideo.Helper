from __future__ import annotations

"""Regression: updating from a live Helper must not become uninstall-only."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
backend = (root / "installer_next/backend_windows.go").read_text(encoding="utf-8")
wizard = (root / "installer_next/main_windows.go").read_text(encoding="utf-8")
updater = (root / "linkvideo_vpn_helper/services/update_service.py").read_text(encoding="utf-8")

# The updater must not pass the running Helper installation directory as CWD.
assert 'os.startfile(str(setup_path), cwd=safe_cwd)' in updater
assert 'cwd=safe_cwd' in updater
assert 'safe_cwd = tempfile.gettempdir()' in updater

# Check and change the installer's own directory before UAC/elevation. UAC
# creates a second process and must also have a safe explicit working dir.
main = wizard.split("func main() {", 1)[1].split("func hasArg", 1)[0]
assert main.index("detachInstallerWorkingDirectory()") < main.index("ensureElevated()")
assert "LpDirectory:  safeDir" in backend
assert "os.Chdir(tempDir)" in backend

# Never run the uninstall-only wizard after clicking an upgrade choice.
assert "idRemoveInstalled" not in wizard
assert '"Удалить текущую версию"' not in wizard
assert 'hasArg("--uninstall")' in wizard
assert "createUninstallPages()" in wizard

# Do not ignore taskkill results and assume 450 ms is long enough: poll the
# actual Windows process table until all Helper processes have exited.
cleanup = backend.split("func stopHelperProcesses() error {", 1)[1].split(
    "// A full installer", 1)[0]
assert 'runningHelperProcesses()' in cleanup
assert 'taskkill.exe' in cleanup
assert 'return fmt.Errorf("не удалось завершить процессы' in cleanup

install = backend.split("func installProduct(", 1)[1].split(
    "func removeShortcuts", 1)[0]
assert install.count("if err := stopHelperProcesses(); err != nil {") == 2
assert install.index("stageRuntimeSnapshot(dest, progress)") < install.rindex(
    "if err := stopHelperProcesses(); err != nil {"
) < install.index("activateStagedRuntime(dest, staging)")

# Windows lock errors should be bounded retries and preserve the old
# installed tree if no clean rename is possible.
retry = backend.split("func renameRuntimeWithRetry(", 1)[1].split(
    "func isProcessElevated", 1)[0]
assert "os.Rename(from, to)" in retry
assert "12 * time.Second" in retry
assert "code != 5 && code != 32 && code != 33" in retry
assert "os.RemoveAll" not in retry
assert "renameRuntimeWithRetry(dest, backup)" in backend
assert "renameRuntimeWithRetry(staging, dest)" in backend

print("CORE TESTS 3.0.19 INSTALLER LOCK HANDLING AND NON-DESTRUCTIVE UPGRADE OK")
