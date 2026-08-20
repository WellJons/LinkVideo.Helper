from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

import linkvideo_vpn_helper.services.routeros_search_compat as compat
import linkvideo_vpn_helper.services.search_service_core as core
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials, VPNService


class FakeRouterOSAPIClient:
    """Simulates a RouterOS that accepts query words but returns no rows."""

    nat_rows = [
        {
            ".id": "*87",
            "dst-port": "13513",
            "to-addresses": "172.16.6.25",
            "to-ports": "13513",
            "comment": "89244085625",
            "disabled": "no",
        },
        {
            ".id": "*88",
            "dst-port": "13520-13523",
            "to-addresses": "172.16.6.30",
            "to-ports": "13520-13523",
            "comment": "",
            "disabled": "no",
        },
    ]
    secrets = [
        {
            ".id": "*S1",
            "name": "89244085625",
            "remote-address": "",
            "profile": "89244085625",
        },
        {
            ".id": "*S2",
            "name": "89234232712_116",
            "remote-address": "",
            "profile": "89234232712_116",
        },
    ]
    profiles = [
        {
            ".id": "*P1",
            "name": "89244085625",
            "remote-address": "172.16.6.25",
        },
        {
            ".id": "*P2",
            "name": "89234232712_116",
            "remote-address": "172.16.6.30",
        },
    ]

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def print(self, path: str, params=None):
        params = params or {}
        # Broken/legacy behavior being guarded against: filtered query returns
        # empty without throwing, even though the row exists in the menu.
        if any(str(key).startswith("?") for key in params):
            return []
        if path == "/ip/firewall/nat":
            return [dict(row) for row in self.nat_rows]
        if path == "/ppp/secret":
            return [dict(row) for row in self.secrets]
        if path == "/ppp/profile":
            return [dict(row) for row in self.profiles]
        return []


core.RouterOSAPIClient = FakeRouterOSAPIClient
compat.RouterOSAPIClient = FakeRouterOSAPIClient
compat._INSTALLED = False
compat.install_routeros_search_compat()

service = VPNService()
search = core.FastSearchService(service)
creds = SessionCredentials("AdminChats", "x")

# Exact NAT row from the reported regression must still be found when the
# RouterOS ?dst-port query returns an empty list.
hints = search._server_port_hint("vpn06.linkvideo.ru", creds, 13513)
assert "89244085625" in hints, hints
assert "@remote:172.16.6.25" in hints, hints

# Ranged dst-port rules are also invisible to an exact ?dst-port= query; local
# fallback filtering must understand the range.
range_hints = search._server_port_hint("vpn06.linkvideo.ru", creds, 13523)
assert "@remote:172.16.6.30" in range_hints, range_hints

# If an old NAT rule has no useful comment, owner resolution via PPP Profile
# must also fall back from an empty exact RouterOS query to a local scan.
owners = search._logins_for_remote("vpn06.linkvideo.ru", creds, "172.16.6.30")
assert owners == ["89234232712_116"], owners

app_text = (root / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
assert "install_routeros_search_compat" in app_text

print("CORE TESTS 3.0.8 ROUTEROS PORT SEARCH COMPAT OK")
