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

    # Protect against the exact regression seen in the RC: the cloud archive
    # calls _render_deleted_client. A patch on _render_deleted is never invoked.
    target = "SearchManagePage._render_deleted_client = render_deleted"
    assert target in ux
    assert "SearchManagePage._render_deleted = render_deleted" not in ux
    assert target in archive

    # Audit page defaults to employee actions and no longer live-rebuilds the
    # entire 300-row table for every RouterOS /listen event.
    assert '"Журнал действий"' in ux
    assert 'self.source_filter.addItem("Действия сотрудников", "desktop")' in ux
    assert "self.cloud.activity(120" in ux
    assert 'def start_stream(self) -> None:' in ux
    assert "iter_activity_events" not in ux
    assert 'str(row.get("action") or "") != "auth.login"' in ux

    # VPN Servers keeps the infrastructure overview but removes duplicate
    # lifecycle/expert controls from the default surface.
    for name in ("m_installed", "m_quarantine", "start_all_btn", "stop_all_btn", "policy"):
        assert f'"{name}"' in ux
    assert 'text == "Памятка состояний VPN-клиентов"' in ux
    assert 'status.setText("Google Sheets · ручной резерв")' in ux
    assert 'button.setText("Создать резерв")' in ux

    # Final polish must be installed only after legacy/Sheets page wrappers have
    # been composed, and the navigation name must explain the page's purpose.
    assert "install_operator_ux_polish" in sheets
    assert sheets.index("VPNServersPage._build = build") < sheets.index("install_operator_ux_polish")
    assert '("vpn_activity", "≡", "Журнал действий", "Кто и что менял в VPN-клиентах")' in nav

    print("OPERATOR_UX_POLISH_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
