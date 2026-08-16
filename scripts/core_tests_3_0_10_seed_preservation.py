from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.services import vpn_retention_policy as policy
from linkvideo_vpn_helper.services import vpn_automation_service as public
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials
import linkvideo_vpn_helper.mikrotik.api_ssl_client as api_module


created_ns = time.time_ns() - 250 * policy.DAY_NS
original_comment = policy.compose_extended_comment("operator note", "U", 0, created_ns, "never_active_tracking")


class FakeAPI:
    secrets = [{
        ".id": "*1",
        "name": "never-active",
        "disabled": "no",
        "last-logged-out": "",
        "comment": original_comment,
    }]

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def print(self, path, params=None):
        if path == "/ppp/secret":
            return [dict(row) for row in self.secrets]
        if path == "/ppp/active":
            return []
        return []

    def set(self, path, rid, params):
        assert path == "/ppp/secret"
        for row in self.secrets:
            if row.get(".id") == rid:
                row.update(dict(params))
                return
        raise AssertionError(rid)


real_policy_api = policy.RouterOSAPIClient
real_module_api = api_module.RouterOSAPIClient
policy.RouterOSAPIClient = FakeAPI
api_module.RouterOSAPIClient = FakeAPI
try:
    policy._INSTALLED = False
    policy.install_retention_policy()
    from linkvideo_vpn_helper.services import vpn_retention_seed_guard as guard
    guard._INSTALLED = False
    guard.install_retention_seed_guard()

    service = public.VPNAutomationService()
    result = service.seed_lifecycle("vpn-test", SessionCredentials("u", "p"))
    assert result.total == 1
    # Initialization is non-destructive and state Q is reserved for a PPP Secret
    # that is really disabled. Old enabled candidates stay sleeping until LV-Aging
    # is explicitly enabled and applies the policy.
    assert result.sleeping == 1
    assert result.quarantine == 0
    assert FakeAPI.secrets[0]["disabled"] == "no"

    final = FakeAPI.secrets[0]["comment"]
    meta = policy.parse_extended_comment(final)
    expected_created_day = created_ns // policy.DAY_NS
    actual_created_day = meta.created_ns // policy.DAY_NS
    assert actual_created_day == expected_created_day, (actual_created_day, expected_created_day, final)
    assert meta.base_comment == "operator note"
    assert meta.state == "S" and meta.reason == "inactive_30"
    assert "|LV2|" in final and "|c=" in final and "|r=s|" in final
finally:
    policy.RouterOSAPIClient = real_policy_api
    api_module.RouterOSAPIClient = real_module_api

print("CORE TESTS 3.0.10 SEED PRESERVATION OK")
