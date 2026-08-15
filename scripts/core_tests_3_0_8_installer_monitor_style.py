from __future__ import annotations

from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "installer_next/main_windows.go").read_text(encoding="utf-8")

    # Keep the same visual grammar as the production LinkVideo.Monitor wizard.
    required = (
        "clientWidth  = 900",
        "clientHeight = 580",
        "leftWidth    = 266",
        "bsOwnerDraw",
        "drawBenefitCard",
        "drawRequirementsCard",
        "rgb(255, 173, 25)",
        'drawText(hdc, "LinkVideo"',
        'drawText(hdc, "HELPER"',
        '"Рабочие"',
        '"инструменты"',
        '"VPN-клиенты"',
        '"Архив"',
        '"Инфраструктура"',
    )
    for marker in required:
        assert marker in source, f"installer lost Monitor-style marker: {marker}"

    # Helper must reuse Monitor's visual language, not its capture/service logic.
    forbidden = (
        "installUACServiceWorker",
        "stopCaptureServiceForUpgrade",
        "MediaMTX",
        "LinkVideo Monitor",
        "Запись экрана",
    )
    for marker in forbidden:
        assert marker not in source, f"Monitor-specific behaviour leaked into Helper installer: {marker}"

    print("CORE TESTS 3.0.8 MONITOR-STYLE HELPER INSTALLER OK")


if __name__ == "__main__":
    main()
