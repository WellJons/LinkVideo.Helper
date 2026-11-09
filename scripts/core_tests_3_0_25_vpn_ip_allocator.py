from __future__ import annotations

"""Offline regression for manually reserved high VPN Remote Address.

No real RouterOS or customer VPN accounts are touched.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from linkvideo_vpn_helper.services.vpn_address_allocator import (
    DEFAULT_START_IP, next_free_address, used_remote_addresses, validate_start_ip,
)
from linkvideo_vpn_helper.services.vpn_service import VPNService, SessionCredentials
from linkvideo_vpn_helper.services.server_registry import ServerRegistry
from linkvideo_vpn_helper.ui.pages.create_client_page import CreateClientPage


assert DEFAULT_START_IP == "172.16.0.1"  # start of the historical VPN source tables
assert next_free_address({"172.16.1.130"}, DEFAULT_START_IP) == "172.16.0.1"
assert next_free_address({"172.16.0.1", "172.16.0.2", "172.16.1.130"}, DEFAULT_START_IP) == "172.16.0.3"
assert next_free_address({"172.16.1.130"}, "172.16.0.5") == "172.16.0.5"
assert next_free_address({"172.168.1.130"}, "172.168.0.5") == "172.168.0.5"
assert next_free_address({"172.16.0.5", "172.16.0.6", "172.16.1.130"}, "172.16.0.5") == "172.16.0.7"
assert next_free_address({"172.16.0.254"}, "172.16.0.254") == "172.16.1.1"
assert next_free_address({"172.16.1.176", "172.16.1.177"}, DEFAULT_START_IP) == "172.16.1.178"
assert next_free_address({"172.168.1.130"}, "172.16.0.5") == "172.16.0.5"
assert VPNService()._next_ip("172.168.1.254") == "172.168.2.1"

for bad in ("", "garbage", "172.16.0.0", "172.16.0.255", "172.16.0.256", "2001:db8::1"):
    try:
        validate_start_ip(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Invalid configured start must be rejected: {bad!r}")

snapshot = {
    "secrets": [{"name": "manual", "remote-address": "172.16.1.130"}],
    "profiles": [
        {"name": "profile-low", "remote-address": "172.16.0.5"},
        {"name": "pool-name", "remote-address": "POOL_PRIVATE"},
        {"name": "gateway", "local-address": "172.16.0.1"},
    ],
    "actives": [{"name": "active", "address": "172.16.0.6"}],
    "nat_rules": [{"comment": "old-removed", "to-addresses": "172.16.0.7"}],
}
occupied = used_remote_addresses(snapshot)
assert occupied == {"172.16.1.130", "172.16.0.5", "172.16.0.6", "172.16.0.7", "172.16.0.1"}
assert next_free_address(occupied, "172.16.0.5") == "172.16.0.8"
assert VPNService()._collect_used_remote_addresses(snapshot) == occupied


class FakeAPI:
    current = {
        "/ppp/secret": [{"name": "manual", "remote-address": "172.168.1.130"}],
        "/ppp/profile": [{"name": "manual", "remote-address": "172.168.1.130"}],
        "/ppp/active": [],
        "/ip/firewall/nat": [],
    }

    def __init__(self, *args, **kwargs):
        self.rows = {path: [dict(row) for row in values] for path, values in self.current.items()}
        self.index = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def print(self, path, params=None):
        rows = self.rows.get(path, [])
        if params and "?dst-port=" in params:
            return [dict(r) for r in rows if str(r.get("dst-port", "")) == str(params["?dst-port="])]
        return [dict(r) for r in rows]

    def add(self, path, params):
        self.index += 1
        uid = f"*{self.index}"
        self.rows.setdefault(path, []).append({".id": uid, **params})
        return uid

    def remove(self, path, uid):
        self.rows[path] = [r for r in self.rows[path] if r.get(".id") != uid]


service = VPNService()
with patch("linkvideo_vpn_helper.services.vpn_service.RouterOSAPIClient", FakeAPI):
    accounts = service.create_clients_batch(
        "vpn01.test", SessionCredentials("admin", "placeholder"),
        "test_new", 1, 2, start_ip="172.168.0.5",
    )
assert [client.remote_address for client in accounts] == ["172.168.0.5", "172.168.0.6"]
assert all(client.ports for client in accounts)
assert len({client.remote_address for client in accounts}) == len(accounts)


app = QApplication.instance() or QApplication([])
with TemporaryDirectory() as directory:
    settings = QSettings(str(Path(directory) / "test.ini"), QSettings.Format.IniFormat)
    registry = ServerRegistry(settings)
    page = CreateClientPage(VPNService(), None, SessionCredentials("admin", "placeholder"), registry)
    assert page.ip_start.text() == DEFAULT_START_IP
    one = page.server_picker.host()
    page.ip_start.setText("172.16.0.5")
    page._save_ip_start()
    assert settings.value(page._ip_start_key(one)) == "172.16.0.5"

    other = next(host for host in registry.hosts() if host != one)
    # The legacy default may have been implicitly persisted on another server.
    settings.setValue(page._ip_start_key(other), "172.16.1.176")
    page.server_picker.setHost(other)
    assert page.ip_start.text() == DEFAULT_START_IP
    page.ip_start.setText("172.16.0.9")
    page._save_ip_start()
    page.server_picker.setHost(one)
    assert page.ip_start.text() == "172.16.0.5"
    page.server_picker.setHost(other)
    assert page.ip_start.text() == "172.16.0.9"
    page.close()

print("CORE TESTS 3.0.25 REMOTE ADDRESS PER SERVER FIRST FREE HIGH OUTLIER OK")
