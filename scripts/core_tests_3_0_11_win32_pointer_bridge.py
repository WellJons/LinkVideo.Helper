from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "installer_next" / "main_windows.go"


def main() -> None:
    source = MAIN.read_text(encoding="utf-8")

    # go vet's unsafeptr analyzer cannot know that WM_DRAWITEM lParam is a
    # Windows-owned pointer to DRAWITEMSTRUCT. This exact conversion is required
    # by the Win32 callback ABI. Keep it narrowly allowlisted so disabling only
    # that analyzer for installer_next cannot hide newly introduced uintptr ->
    # pointer bridges.
    bridge = "(*drawItemStruct)(unsafe.Pointer(lParam))"
    assert source.count(bridge) == 1, "WM_DRAWITEM LPARAM bridge changed; review unsafe interop explicitly"

    suspicious = []
    for lineno, line in enumerate(source.splitlines(), 1):
        if "unsafe.Pointer(lParam)" in line and bridge not in line:
            suspicious.append(f"{lineno}: {line.strip()}")
    assert not suspicious, "unexpected LPARAM pointer conversion(s): " + "; ".join(suspicious)

    audit = (ROOT / "scripts" / "audit_go.ps1").read_text(encoding="utf-8")
    assert "go vet -unsafeptr=false ./..." in audit
    assert audit.count("go vet ./...") == 2, "patcher/silent_updater must retain full vet"

    workflow = (ROOT / ".github" / "workflows" / "windows-build.yml").read_text(encoding="utf-8")
    assert "scripts/verify_release.ps1" in workflow
    verifier = (ROOT / "scripts" / "verify_release.ps1").read_text(encoding="utf-8")
    assert "scripts\\audit_go.ps1" in verifier, "CI must reach the authoritative Go audit"

    print("CORE TESTS 3.0.11 WIN32 POINTER BRIDGE OK")


if __name__ == "__main__":
    main()
