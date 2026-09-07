from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    path = "linkvideo_vpn_helper/ui/cloud_activity_bridge.py"
    source = read(path)
    ast.parse(source, filename=path)

    assert '"create_clients_batch": "client.create"' in source
    assert '"set_password": "client.password_change"' in source
    assert '"ports_per_client"' in source
    assert '"accounts_count"' in source
    assert '"remote_address"' in source
    assert '"ports"' in source
    assert 'return {"password_changed": True}' in source
    assert '"session_removed"' in source
    assert "Never include the old/new password itself" in source
    assert 'details["error"]' in source

    print("EMPLOYEE_ACTIVITY_DETAILS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
