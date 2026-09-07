from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    ux_path = "linkvideo_vpn_helper/ui/operator_ux_polish.py"
    sheets_path = "linkvideo_vpn_helper/ui/vpn_sheets_emergency_only.py"
    nav_path = "linkvideo_vpn_helper/ui/cloud_activity_nav.py"
    archive_path = "linkvideo_vpn_helper/ui/cloud_archive_search_integration.py"

    ux = read(ux_path)
    sheets = read(sheets_path)
    nav = read(nav_path)
    archive = read(archive_path)

    ast.parse(ux, filename=ux_path)
    ast.parse(sheets, filename=sheets_path)
    ast.parse(nav, filename=nav_path)
    ast.parse(archive, filename=archive_path)

    # Deleted-client card is operator-facing and Escape also closes archive cards.
    assert 'getattr(self, "_deleted_current", None)' in ux
    assert 'self._deleted_current = None' in ux
    assert 'StatusPill("Можно восстановить", "success")' in ux
    assert 'restore = QPushButton("Восстановить")' in ux
    assert "Восстановление выполняется из PostgreSQL" not in ux
    assert '"Причина удаления"' in ux

    # Protect against the exact RC regression: cloud archive actually invokes
    # _render_deleted_client, so patching _render_deleted has no visible effect.
    target = "SearchManagePage._render_deleted_client = render_deleted"
    assert target in ux
    assert "SearchManagePage._render_deleted = render_deleted" not in ux
    assert target in archive

    # The old global audit page implementation may remain as dormant support
    # code, but it must not be exposed in the desktop navigation. Audit records
    # continue to be collected in PostgreSQL for future per-client history.
    assert 'item[0] != "vpn_activity"' in nav
    assert "items.insert(" not in nav
    assert "VPNActivityPage" not in nav
    assert "install_cloud_archive_search" in nav
    assert "install_cloud_archive_completion_compat" in nav

    # VPN Servers keeps the infrastructure overview but removes duplicate
    # lifecycle/expert controls from the default surface.
    for name in ("m_installed", "m_quarantine", "start_all_btn", "stop_all_btn", "policy"):
        assert f'"{name}"' in ux
    assert 'text == "Памятка состояний VPN-клиентов"' in ux
    assert 'status.setText("Google Sheets · ручной резерв")' in ux
    assert 'button.setText("Создать резерв")' in ux

    # Final polish must be installed only after legacy/Sheets page wrappers have
    # been composed.
    assert "install_operator_ux_polish" in sheets
    assert sheets.index("VPNServersPage._build = build") < sheets.index("install_operator_ux_polish")

    print("OPERATOR_UX_POLISH_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
