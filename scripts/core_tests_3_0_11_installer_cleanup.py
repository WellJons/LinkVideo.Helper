from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "installer_next" / "backend_windows.go"


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

    print("CORE TESTS 3.0.11 INSTALLER CLEANUP OK")


if __name__ == "__main__":
    main()
