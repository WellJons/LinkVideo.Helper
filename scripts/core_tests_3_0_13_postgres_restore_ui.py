from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def parse(path: str) -> None:
    ast.parse(read(path), filename=path)


def main() -> int:
    files = (
        "linkvideo_vpn_helper/app.py",
        "linkvideo_vpn_helper/ui/cloud_archive_search_integration.py",
        "linkvideo_vpn_helper/services/cloud_http_error_compat.py",
        "linkvideo_vpn_helper/ui/cloud_activity_nav.py",
        "linkvideo_vpn_helper/ui/vpn_sheets_emergency_only.py",
        "server/linkvideo_vpnsync/archive_restore.py",
        "server/linkvideo_vpnsync/api.py",
    )
    for path in files:
        parse(path)

    app = read("linkvideo_vpn_helper/app.py")
    assert "install_cloud_http_error_details" in app
    assert "install_cloud_activity_nav()" in app

    archive_ui = read("linkvideo_vpn_helper/ui/cloud_archive_search_integration.py")
    assert 'QPushButton("Восстановить клиента")' in archive_ui
    assert '"/v1/deleted/search"' in archive_ui
    assert '"/v1/archive/restore/preflight"' in archive_ui
    assert '"/v1/archive/restore"' in archive_ui
    assert "Ищу логин в архиве PostgreSQL после проверки живых MikroTik" in archive_ui
    assert "операция полностью блокируется" in archive_ui
    assert "порты автоматически не заменяются" in archive_ui

    sheets_ui = read("linkvideo_vpn_helper/ui/vpn_sheets_emergency_only.py")
    assert "restore_button.hide()" in sheets_ui
    assert "restore_button.setEnabled(False)" in sheets_ui

    server_restore = read("server/linkvideo_vpnsync/archive_restore.py")
    assert 'api.print("/ppp/secret")' in server_restore
    assert 'api.print("/ppp/profile")' in server_restore
    assert 'api.print("/ip/firewall/nat")' in server_restore
    assert 'raise ArchiveRestoreConflict("Restore blocked by live RouterOS conflicts"' in server_restore
    assert "Recheck the exact external port immediately before add" in server_restore
    assert "for path, item_id in reversed(created):" in server_restore
    assert "len(secrets) == 0" in server_restore

    error_compat = read("linkvideo_vpn_helper/services/cloud_http_error_compat.py")
    assert "response.status_code >= 400" in error_compat
    assert "Восстановление заблокировано живым MikroTik" in error_compat

    print("POSTGRES_RESTORE_UI_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
