from __future__ import annotations

"""Regression for the production client-creation crash in 3.0.26.

Executes both NAT safety decorators with a mocked RouterOS, including the
required keyword-only start_ip. No network or live customer account touched.
"""

import inspect
from unittest.mock import patch

from linkvideo_vpn_helper.services.vpn_service import (
    ClientRecord, SessionCredentials, VPNService,
)
from linkvideo_vpn_helper.services import nat_conflict_compat, nat_inventory_compat

CREDS = SessionCredentials("test", "offline")
calls = []


class OfflineRouterOS:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def print(self, path, *_args, **_kwargs):
        return []


def base_create(self, server, creds, base_login, ports_per_client,
                accounts_count, progress_callback=None, cancel_event=None, *,
                start_ip="172.16.0.1"):
    calls.append((server, base_login, ports_per_client, accounts_count, start_ip))
    return [ClientRecord(
        server=server, login=base_login, password="OFFLINE-ONLY",
        remote_address=start_ip, ports=[10017], profile_id="*p",
        secret_id="*s", nat_rule_ids=["*n"], is_enabled=True,
    )]


original = VPNService.create_clients_batch
try:
    with patch.object(VPNService, "create_clients_batch", base_create):
        with patch.object(nat_conflict_compat, "RouterOSAPIClient", OfflineRouterOS):
            nat_conflict_compat._INSTALLED = False
            nat_conflict_compat.install_nat_conflict_compat()
            nat_wrapped = VPNService.create_clients_batch
            assert "start_ip" in inspect.signature(nat_wrapped).parameters
            res = VPNService().create_clients_batch(
                "vpn01.test", CREDS, "new", 1, 1,
                start_ip="172.16.0.8",
            )
            assert calls[-1][-1] == "172.16.0.8"
            assert res[0].remote_address == "172.16.0.8"

            # A second existing wrapper can also be enabled. The old version
            # swallowed the same keyword argument and must be fixed too.
            nat_inventory_compat._INSTALLED = False
            nat_inventory_compat.install_nat_inventory_compat()
            assert "start_ip" in inspect.signature(VPNService.create_clients_batch).parameters

            def snapshot(_self, _host, _creds):
                return {"secrets": [], "profiles": [], "actives": [], "nat_rules": []}

            def records(_self, _server, _snapshot):
                latest = calls[-1]
                return [ClientRecord(
                    server=latest[0], login=latest[1], password="OFFLINE-ONLY",
                    remote_address=latest[-1], ports=[10017],
                    profile_id="*p", secret_id="*s", nat_rule_ids=["*n"],
                    is_enabled=True,
                )]

            with patch.object(VPNService, "fetch_config_snapshot", snapshot), \
                 patch.object(VPNService, "_build_client_records", records):
                checked = VPNService().create_clients_batch(
                    "vpn02.test", CREDS, "another", 1, 1,
                    start_ip="172.16.0.19",
                )
                assert calls[-1][-1] == "172.16.0.19", calls
                assert checked[0].remote_address == "172.16.0.19"
                automatic = VPNService().create_clients_batch(
                    "vpn03.test", CREDS, "default", 1, 1,
                )
                assert calls[-1][-1] == "172.16.0.1"
                assert automatic[0].remote_address == "172.16.0.1"
finally:
    VPNService.create_clients_batch = original
    nat_conflict_compat._INSTALLED = False
    nat_inventory_compat._INSTALLED = False

print("CORE TESTS 3.0.27 CLIENT CREATION WRAPPER + UI DEFAULT OK")
