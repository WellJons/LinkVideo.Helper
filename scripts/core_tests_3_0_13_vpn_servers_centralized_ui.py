from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    ui_path = "linkvideo_vpn_helper/ui/vpn_servers_centralized_ui.py"
    sheets_path = "linkvideo_vpn_helper/ui/vpn_sheets_emergency_only.py"
    guard_path = "linkvideo_vpn_helper/services/central_retention_guard.py"

    ui = read(ui_path)
    sheets = read(sheets_path)
    guard = read(guard_path)

    ast.parse(ui, filename=ui_path)
    ast.parse(sheets, filename=sheets_path)
    ast.parse(guard, filename=guard_path)

    # Obsolete RouterOS LV controls and status column must not be visible.
    for name in ("install_all_btn", "start_all_btn", "stop_all_btn", "m_installed", "m_quarantine"):
        assert f'"{name}"' in ui
    assert "table.setColumnHidden(7, True)" in ui
    assert 'backup.setText("Резервная копия серверов")' in ui

    # Refresh should inspect only real infrastructure state, not the retired
    # per-router LV Scheduler implementation.
    assert "self.service.analyze_server_quick" in ui
    assert "automation.get_status" not in ui

    # Per-server detail is intentionally infrastructure-only.
    assert 'QPushButton("Резервная копия этого сервера")' in ui
    assert 'QPushButton("Обновить LV")' not in ui
    assert 'QPushButton("Установить LV")' not in ui
    assert 'QPushButton("Запустить LV")' not in ui
    assert 'QPushButton("Включить карантин")' not in ui

    # Final hook is installed after the legacy/Sheets wrappers and operator polish.
    assert "install_vpn_servers_centralized_ui" in sheets
    assert sheets.index("install_operator_ux_polish()") < sheets.index("install_vpn_servers_centralized_ui()")

    # Any unreachable legacy call still fails closed, but uses current product branding.
    assert "LinkVideo.Cloud" in guard
    assert "LinkVideo.VPNSync" not in guard

    print("VPN_SERVERS_CENTRALIZED_UI_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
