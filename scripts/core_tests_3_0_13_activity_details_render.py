from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    compat_path = "linkvideo_vpn_helper/ui/vpn_activity_details_compat.py"
    nav_path = "linkvideo_vpn_helper/ui/cloud_activity_nav.py"
    compat = read(compat_path)
    nav = read(nav_path)
    ast.parse(compat, filename=compat_path)
    ast.parse(nav, filename=nav_path)

    # Audit formatting support remains available for future per-client history,
    # but the standalone activity page is intentionally not installed in nav.
    assert "install_vpn_activity_details" not in nav
    assert 'item[0] != "vpn_activity"' in nav

    assert '"archive": "Восстановление"' in compat
    assert '"retention": "Автоматика"' in compat
    assert '"archive.restore": "Восстановление VPN-клиента"' in compat
    assert '"retention.quarantine": "Карантин VPN-клиента"' in compat
    assert 'action == "client.create"' in compat
    assert 'action == "nat.add_ports"' in compat
    assert 'action == "nat.remove_port"' in compat
    assert 'action == "client.password_change"' in compat
    assert "значение пароля в историю не записывается" in compat
    assert 'action == "client.enabled_change"' in compat
    assert 'action == "nat.enabled_change"' in compat
    assert 'action == "client.disconnect"' in compat
    assert 'action == "client.delete"' in compat
    assert 'action.startswith("retention.")' in compat

    print("ACTIVITY_DETAILS_RENDER_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
