from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STOPPER = ROOT / "patcher" / "process_stop_windows.go"
SILENT = ROOT / "patcher" / "silent_mode_windows.go"
NATIVE_VERSION = ROOT / "patcher" / "native_version_windows.go"


def main() -> int:
    stop = STOPPER.read_text(encoding="utf-8")
    silent = SILENT.read_text(encoding="utf-8")
    native = NATIVE_VERSION.read_text(encoding="utf-8")

    required_stop_fragments = (
        "func stopHelperVerified() error",
        "tasklist.exe",
        "taskkill.exe",
        "time.Now().Add(15 * time.Second)",
        "LinkVideo.Helper.exe",
        "LinkVideo.Helper.Updater.exe",
        "time.Sleep(350 * time.Millisecond)",
    )
    for fragment in required_stop_fragments:
        assert fragment in stop, fragment

    assert "if err := stopHelperVerified(); err != nil" in silent
    assert "stopHelper()" not in silent
    assert "Never touch Program Files until Windows confirms" in silent

    assert silent.count("nativeProductVersion(appPath)") == 2
    assert "productVersion(appPath)" not in silent
    for fragment in (
        'syscall.NewLazyDLL("version.dll")',
        'GetFileVersionInfoSizeW',
        'GetFileVersionInfoW',
        'VerQueryValueW',
        'func nativeProductVersion(path string) (string, error)',
        'ProductVersionMS',
        'ProductVersionLS',
    ):
        assert fragment in native, fragment
    assert "powershell.exe" not in native.lower()

    print("CORE TESTS 3.0.11 VERIFIED PROCESS STOP + NATIVE VERSION OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
