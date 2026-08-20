from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "installer_next" / "backend_windows.go"
SELFTEST = ROOT / "installer_next" / "selftest_windows.go"


def main() -> None:
    source = BACKEND.read_text(encoding="utf-8")

    install_body = source.split("func installProduct", 1)[1]
    for marker in (
        "recoverInterruptedRuntimeUpgrade(dest)",
        "stageRuntimeSnapshot(dest, progress)",
        "activateStagedRuntime(dest, staging)",
        "verifyRuntimeSnapshot(dest)",
        "rollbackActivatedRuntime(dest, backup)",
    ):
        assert marker in install_body, f"transactional installer path missing {marker}"
    assert install_body.index("stageRuntimeSnapshot(dest, progress)") < install_body.index(
        "activateStagedRuntime(dest, staging)"
    )
    assert install_body.index("removeSilentUpdateTask()") < install_body.index("stopHelperProcesses()")
    for process_name in (
        '"LinkVideo.Helper.Updater.Worker.exe"',
        '"official-patch.exe"',
        '"LinkVideo.Helper_Patch_Update.exe"',
    ):
        assert process_name in source, f"installer does not stop update race process {process_name}"

    transaction = source.split("func verifyRuntimeSnapshot", 1)[1].split("func extractPayload", 1)[0]
    for required in (
        '"LinkVideo.Helper.Updater.exe"',
        '"LinkVideo VPN Helper.exe"',
        '"Uninstall.exe"',
        'dest + ".rollback"',
        'os.Rename(dest, backup)',
        'os.Rename(staging, dest)',
    ):
        assert required in transaction, f"installer transaction missing {required}"

    # The runtime transaction is strictly scoped to Program Files. User state
    # and the downloaded FFmpeg cache live in LocalAppData and survive upgrade.
    assert "LOCALAPPDATA" not in transaction
    assert "APPDATA" not in transaction
    assert "removeUserData" not in transaction

    selftest = SELFTEST.read_text(encoding="utf-8")
    for marker in (
        'hasArg("--self-test")',
        "installerSelfTest()",
        "stageRuntimeSnapshot(dest, nil)",
        "activateStagedRuntime(dest, staging)",
        "recoverInterruptedRuntimeUpgrade(dest)",
        '"LinkVideo.Helper.Updater.exe"',
        '"Uninstall.exe"',
        'strings.EqualFold(entry.Name(), "ffmpeg.exe")',
    ):
        assert marker in selftest, f"installer self-test missing {marker}"

    # Self-test must finish before main() can request elevation or touch the
    # installed product. init() + os.Exit keeps this verification side-effect free.
    assert "func init()" in selftest
    assert "os.Exit(20)" in selftest and "os.Exit(0)" in selftest

    print("CORE TESTS 3.0.11 INSTALLER CLEANUP/SELF-TEST OK")


if __name__ == "__main__":
    main()
