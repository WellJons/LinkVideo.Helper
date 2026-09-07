from __future__ import annotations

import time
from pathlib import Path

from linkvideo_vpn_helper.services.search_service import FastSearchService
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials
from linkvideo_vpn_helper.ui.request_completion_compat import install_request_completion_compat


class DummyVPNService:
    pass


def main() -> None:
    install_request_completion_compat()

    service = FastSearchService(DummyVPNService())
    creds = SessionCredentials("test", "test", timeout=0.1)
    seen: list[tuple[int, int, str]] = []

    started = time.monotonic()

    def worker(server: str):
        # This is deliberately much longer than the obsolete compatibility
        # deadline argument below. A wall-clock deadline would incorrectly
        # synthesize failures; request-driven completion must return both rows.
        time.sleep(0.04 if server == "vpn01" else 0.07)
        return server + ":done"

    results, errors, checked = service._daemon_server_calls(
        ["vpn01", "vpn02"],
        worker,
        creds=creds,
        progress=lambda a, b, server: seen.append((a, b, server)),
        deadline_seconds=0.001,
    )
    elapsed = time.monotonic() - started

    assert checked == 2, checked
    assert not errors, errors
    assert {server for server, _payload in results} == {"vpn01", "vpn02"}
    assert len(seen) == 2 and seen[-1][0] == 2 and seen[-1][1] == 2, seen
    assert elapsed >= 0.06, elapsed

    root = Path(__file__).resolve().parents[1]
    compat = (root / "linkvideo_vpn_helper/ui/request_completion_compat.py").read_text(encoding="utf-8")
    assert "deadline_at" not in compat
    assert "synthetic timeout" in compat
    assert "self._close_busy_dialog()" in compat

    archive = (root / "linkvideo_vpn_helper/ui/cloud_archive_completion_compat.py").read_text(encoding="utf-8")
    assert "self._deleted_lookup_pending = False" in archive
    assert "Архив удалённых недоступен" in archive

    auth_message = (root / "linkvideo_vpn_helper/services/cloud_auth_message_compat.py").read_text(encoding="utf-8")
    assert "Сервер VPNSync доступен" in auth_message
    assert "SSH (Termius)" in auth_message

    operator = (root / "server/manage_operator.py").read_text(encoding="utf-8")
    assert "separate from the Ubuntu/SSH account" in operator
    assert "PBKDF2 hash" in operator

    installer = (root / "server/install_ubuntu.sh").read_text(encoding="utf-8")
    assert "SSH/Termius credentials are separate" in installer
    assert "manage_operator.py --username <login> --role admin" in installer

    audit_bridge = (root / "linkvideo_vpn_helper/ui/cloud_activity_bridge.py").read_text(encoding="utf-8")
    assert 'settings.value("username"' in audit_bridge
    assert 'audit_details["employee"]' in audit_bridge

    activity_ui = (root / "linkvideo_vpn_helper/ui/vpn_activity_details_compat.py").read_text(encoding="utf-8")
    assert 'details.get("employee")' in activity_ui
    assert 'row["actor"] = employee' in activity_ui

    print("CORE TESTS 3.0.13 REQUEST-DRIVEN SEARCH/ARCHIVE COMPLETION OK")


if __name__ == "__main__":
    main()
