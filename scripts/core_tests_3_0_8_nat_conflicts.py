from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

import linkvideo_vpn_helper.services.nat_conflict_compat as compat
from linkvideo_vpn_helper.services.vpn_service import ClientRecord, SessionCredentials, VPNService


class FakeRouterOSAPIClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def print(self, path: str, params=None):
        if path == "/ip/firewall/nat":
            return [
                {".id": "*OWN", "chain": "dstnat", "protocol": "tcp", "dst-port": "12500", "to-addresses": "172.16.1.20", "to-ports": "12500", "comment": "890000001", "disabled": "no"},
                {".id": "*OTHER", "chain": "dstnat", "protocol": "tcp", "dst-port": "12500", "to-addresses": "172.16.1.21", "to-ports": "12500", "comment": "", "disabled": "no"},
            ]
        if path == "/ppp/profile":
            return [
                {".id": "*P1", "name": "890000001", "remote-address": "172.16.1.20"},
                {".id": "*P2", "name": "890000002", "remote-address": "172.16.1.21"},
            ]
        if path == "/ppp/secret":
            return [
                {".id": "*S1", "name": "890000001", "profile": "890000001", "remote-address": ""},
                {".id": "*S2", "name": "890000002", "profile": "890000002", "remote-address": ""},
            ]
        return []


compat.RouterOSAPIClient = FakeRouterOSAPIClient
compat._INSTALLED = False
compat.install_nat_conflict_compat()

service = VPNService()
client = ClientRecord(
    server="vpn01.linkvideo.ru",
    login="890000001",
    password="x",
    remote_address="172.16.1.20",
    ports=[12500],
    nat_rule_ids=["*OWN"],
)
conflicts = service.inspect_port_conflicts(
    client.server,
    SessionCredentials("AdminChats", "x"),
    client,
)
assert 12500 in conflicts, conflicts
assert any(row.owner_login == "890000002" for row in conflicts[12500]), conflicts[12500]

source = (root / "linkvideo_vpn_helper/services/nat_conflict_compat.py").read_text(encoding="utf-8")
assert "api.print(\"/ip/firewall/nat\"" in source
assert "VPNService.create_clients_batch = safe_create" in source
assert "VPNService.add_ports = safe_add_ports" in source
assert "self._rollback_create" in source
assert "original_remove_port" in source

print("CORE TESTS 3.0.8 AUTHORITATIVE NAT CONFLICTS OK")
