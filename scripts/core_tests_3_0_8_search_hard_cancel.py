from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from linkvideo_vpn_helper.services.search_service import FastSearchService
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials


class DummyVPN:
    def get_client(self, server, creds, login, include_port_conflicts=True):
        return SimpleNamespace(server=server, login=login)


def main() -> None:
    service = FastSearchService(DummyVPN())

    def matching(server, creds, query):
        if server == "vpn-stuck.example":
            time.sleep(2.0)
            return [query]
        return [query]

    # Replace the RouterOS discovery primitive so this regression is deterministic
    # and does not need a real VPN server.
    service._server_matching_logins = matching
    creds = SessionCredentials("u", "p", 8728, 4.5)

    started = time.monotonic()
    report = service.search_login_all(
        ["vpn-ok.example", "vpn-stuck.example"],
        creds,
        "89950000000",
        deadline_seconds=0.25,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.9, f"interactive search waited for stuck daemon: {elapsed:.2f}s"
    assert report.checked == 2, report.checked
    assert any(x.server == "vpn-ok.example" for x in report.matches)
    assert not any(x.server == "vpn-stuck.example" for x in report.matches)
    assert any(x.server == "vpn-stuck.example" for x in report.errors)

    root = Path(__file__).resolve().parents[1]
    search_source = (root / "linkvideo_vpn_helper/services/search_service.py").read_text(encoding="utf-8")
    assert "ThreadPoolExecutor" not in search_source, "interactive facade reintroduced non-daemon executor"
    assert "daemon=True" in search_source

    cancel_source = (root / "linkvideo_vpn_helper/ui/operation_cancel_guard.py").read_text(encoding="utf-8")
    assert "WindowModality.NonModal" in cancel_source
    assert "cancel_current_action" in cancel_source

    print("CORE TESTS 3.0.8 STUCK SEARCH/CANCEL GUARD OK")


if __name__ == "__main__":
    main()
