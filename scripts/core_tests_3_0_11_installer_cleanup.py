from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "installer_next" / "backend_windows.go"
SELFTEST = ROOT / "installer_next" / "selftest_windows.go"


def main() -> None:
    source = BACKEND.read_text(encoding="utf-8")

    assert "func cleanRuntimeBeforeInstall(dest string) error" in source
    install_body = source.split("func installProduct", 1)[1]
    assert "cleanRuntimeBeforeInstall(dest)" in install_body
    assert install_body.index("cleanRuntimeBeforeInstall(dest)") < install_body.index("extractPayload(dest, progress)")

    cleanup = source.split("func cleanRuntimeBeforeInstall", 1)[1].split("func extractPayload", 1)[0]
    for required in (
        '"_internal"',
        '"tools"',
        '"linkvideo_vpn_helper"',
        '"scripts"',
        'appExeName',
        '"LinkVideo.Helper.Updater.exe"',
        '"LinkVideo VPN Helper.exe"',
        '"updater.exe"',
        '"Uninstall.exe"',
    ):
        assert required in cleanup, f"installer cleanup missing {required}"

    # Full installer cleanup must be scoped to Program Files. User state and the
    # downloaded FFmpeg cache live in LocalAppData and must survive an upgrade.
    assert "LOCALAPPDATA" not in cleanup
    assert "APPDATA" not in cleanup
    assert "removeUserData" not in cleanup

    selftest = SELFTEST.read_text(encoding="utf-8")
    for marker in (
        'hasArg("--self-test")',
        "installerSelfTest()",
        "cleanRuntimeBeforeInstall(dest)",
        "extractPayload(dest, nil)",
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
