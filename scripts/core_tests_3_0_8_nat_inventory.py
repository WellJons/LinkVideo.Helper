from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

import linkvideo_vpn_helper.services.nat_inventory_compat as compat
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials, VPNService


class FakeRouterOSAPIClient:
    login = "89234232712_116"
    profile_name = "legacy-profile-116"
    remote = "172.16.6.30"
    secrets = [{
        ".id": "*S1",
        "name": login,
        "password": "testpass",
        "profile": profile_name,
        "service": "l2tp",
        "disabled": "no",
        "last-logged-out": "aug/16/2026 14:00:00",
        "remote-address": "",
        "comment": "legacy secret",
    }]
    profiles = [{
        ".id": "*P1",
        "name": profile_name,
        "local-address": "172.31.255.254",
        "remote-address": remote,
    }]
    nat_rows = [
        {".id": "*N1", "chain": "dstnat", "action": "dst-nat", "protocol": "tcp", "dst-port": "13510", "to-addresses": remote, "to-ports": "13510", "comment": login, "disabled": "no", "bytes": "720384", "packets": "12009"},
        {".id": "*N2", "chain": "dstnat", "action": "dst-nat", "protocol": "tcp", "dst-port": "13511", "to-addresses": remote, "to-ports": "13511", "comment": "", "disabled": "no", "bytes": "602214", "packets": "9959"},
        {".id": "*N3", "chain": "dstnat", "action": "dst-nat", "protocol": "tcp", "dst-port": "13512", "to-addresses": remote, "to-ports": "13512", "comment": "old manual text", "disabled": "no", "bytes": "611226", "packets": "10104"},
        {".id": "*N4", "chain": "dstnat", "action": "dst-nat", "protocol": "tcp", "dst-port": "13513", "to-addresses": remote, "to-ports": "13513", "comment": "", "disabled": "no", "bytes": "603853", "packets": "10026"},
        {".id": "*RANGE", "chain": "dstnat", "action": "dst-nat", "protocol": "tcp", "dst-port": "12000-12003", "to-addresses": "172.16.6.99", "to-ports": "12000-12003", "comment": "range-owner", "disabled": "no", "bytes": "1", "packets": "1"},
    ]

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def print(self, path: str, params=None):
        params = params or {}
        has_query = any(str(key).startswith("?") for key in params)
        if path == "/ppp/secret":
            if has_query:
                wanted = next((str(value) for key, value in params.items() if str(key).startswith("?name")), "")
                return [dict(row) for row in self.secrets if not wanted or row["name"] == wanted]
            return [dict(row) for row in self.secrets]
        if path == "/ppp/profile":
            if has_query:
                return []
            return [dict(row) for row in self.profiles]
        if path == "/ppp/active":
            return []
        if path == "/ip/firewall/nat":
            if has_query:
                return []
            return [dict(row) for row in self.nat_rows]
        if path == "/interface":
            return []
        return []


compat.RouterOSAPIClient = FakeRouterOSAPIClient
compat._INSTALLED = False
compat.install_nat_inventory_compat()

service = VPNService()
creds = SessionCredentials("AdminChats", "x")

client = service.get_client("vpn06.linkvideo.ru", creds, FakeRouterOSAPIClient.login)
assert client is not None
assert client.remote_address == FakeRouterOSAPIClient.remote, client.remote_address
assert client.ports == [13510, 13511, 13512, 13513], client.ports
assert len(client.nat_rule_ids) == 4, client.nat_rule_ids

assert client.port_nat_bytes[13513] == 603853, client.port_nat_bytes
assert client.port_nat_packets[13513] == 10026, client.port_nat_packets
assert client.port_nat_packets[13511] == 9959, client.port_nat_packets

with FakeRouterOSAPIClient() as api:
    rows = service._api_print_exact(api, "/ip/firewall/nat", "dst-port", "12002", ".id,dst-port")
assert any(row.get(".id") == "*RANGE" for row in rows), rows

used = service._collect_used_ports({"nat_rules": FakeRouterOSAPIClient.nat_rows})
for occupied in (12000, 12001, 12002, 12003, 13510, 13511, 13512, 13513):
    assert occupied in used, (occupied, sorted(used))

source = (root / "linkvideo_vpn_helper/services/nat_inventory_compat.py").read_text(encoding="utf-8")
assert "original_create_clients_batch" in source
assert "snapshot = self.fetch_config_snapshot(server, creds)" in source
assert "expected_ports - actual_ports" in source
assert "self._rollback_create" in source

ui_source = (root / "linkvideo_vpn_helper/ui/nat_counter_integration.py").read_text(encoding="utf-8")
assert "NAT-трафик" in ui_source
assert "Пакеты" in ui_source
assert "Включён" in ui_source and "Отключён" in ui_source
assert "Конфликт" in ui_source
assert 'item.setText("")' in ui_source
assert 'item.setToolTip("")' in ui_source

print("CORE TESTS 3.0.8 COMPLETE NAT INVENTORY OK")
