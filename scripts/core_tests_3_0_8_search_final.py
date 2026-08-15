from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.services.search_service import FastSearchService, SearchReport
from linkvideo_vpn_helper.services.vpn_service import ClientRecord, SessionCredentials


class VPN:
    @staticmethod
    def _normalize_ip(value):
        return str(value or "").strip()

    def get_client(self, server, creds, login, include_port_conflicts=False):
        return ClientRecord(server=server, login=login, password="", remote_address="10.0.0.1", ports=[11313])


svc = FastSearchService(VPN(), max_workers=2)
creds = SessionCredentials("u", "p", timeout=0.1)


def server_logins(server, creds):
    if server == "slow":
        time.sleep(0.7)
        return {"89000000000"}
    return {"89000000000"}


svc._server_logins = server_logins
start = time.monotonic()
login, errors = svc.suggest_free_login_all(["good", "slow"], creds, "89000000000", deadline_seconds=0.2)
elapsed = time.monotonic() - start
assert elapsed < 0.55, elapsed
assert login == "89000000000_1"
assert any(x.server == "slow" for x in errors)


def search_port(server, creds, port):
    if server == "slow":
        time.sleep(0.7)
        return SearchReport(total=1, checked=1)
    report = SearchReport(total=1, checked=1)
    report.matches.append(ClientRecord(server=server, login="89000000000", password="", remote_address="", ports=[port]))
    return report


svc.search_port = search_port
start = time.monotonic()
report = svc.search_port_all(["good", "slow"], creds, 11313, deadline_seconds=0.2)
elapsed = time.monotonic() - start
assert elapsed < 0.55, elapsed
assert any(x.server == "good" for x in report.matches)
assert any(x.server == "slow" for x in report.errors)
assert report.checked == report.total == 2

print("CORE TESTS 3.0.8 ALL-SERVER SEARCH DEADLINES OK")
