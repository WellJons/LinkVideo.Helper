from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.services import vpn_automation_service as vas
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials


class CompatStore:
    menus = {
        "/system/script": [],
        "/system/scheduler": [],
        "/system/logging/action": [],
        "/system/logging": [],
        "/ppp/secret": [],
        "/system/device-mode": [{".id": "*dm", "mode": "advanced", "scheduler": "yes", "flagged": "no"}],
    }
    seq = 1


class CompatAPI:
    """RouterOS variant rejecting optional fields used by newer releases."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    @staticmethod
    def _reject_optional(params):
        for key in ("regex", "dont-require-permissions", "memory-stop-on-full"):
            if key in params:
                raise RuntimeError(f"unknown parameter {key}")

    def print(self, path, extra_params=None):
        return [dict(x) for x in CompatStore.menus.setdefault(path, [])]

    def add(self, path, params):
        self._reject_optional(params)
        rid = f"*{CompatStore.seq}"
        CompatStore.seq += 1
        row = {".id": rid, **{k: str(v) for k, v in params.items()}}
        CompatStore.menus.setdefault(path, []).append(row)
        return rid

    def set(self, path, item_id, params):
        self._reject_optional(params)
        for row in CompatStore.menus.setdefault(path, []):
            if row.get(".id") == item_id:
                row.update({k: str(v) for k, v in params.items()})
                return
        raise RuntimeError(f"missing {path} {item_id}")

    def enable(self, path, item_id):
        self.set(path, item_id, {"disabled": "no"})

    def disable(self, path, item_id):
        self.set(path, item_id, {"disabled": "yes"})


orig_api = vas.RouterOSAPIClient
vas.RouterOSAPIClient = CompatAPI
try:
    auto = vas.VPNAutomationService()
    status = auto.install_or_update("vpn-compat", SessionCredentials("u", "p"))
    assert status.installed, status.installation_detail
    assert status.activity_enabled and status.restore_enabled
    assert not status.aging_enabled

    # Start must also succeed after a complete/partial repair and preserve the
    # safe default: lifecycle quarantine remains opt-in.
    status = auto.set_automation_enabled("vpn-compat", SessionCredentials("u", "p"), True)
    assert status.runtime_enabled
    assert not status.aging_enabled

    scripts = CompatStore.menus["/system/script"]
    assert len(scripts) == 3
    assert all("dont-require-permissions" not in row for row in scripts)
    assert all("policy" in row for row in scripts)  # required permissions were not weakened

    action = CompatStore.menus["/system/logging/action"]
    assert len(action) == 1
    assert "memory-stop-on-full" not in action[0]

    rules = CompatStore.menus["/system/logging"]
    assert len(rules) == 2
    assert all("regex" not in row for row in rules)
    assert {row.get("prefix") for row in rules} == {vas.LV_LOG_PREFIX_PPP, vas.LV_LOG_PREFIX_L2TP}
finally:
    vas.RouterOSAPIClient = orig_api


class DeviceModeAPI:
    def print(self, path):
        if path == "/system/device-mode":
            return [{"mode": "advanced", "scheduler": "no", "flagged": "yes"}]
        return []


hint = vas.VPNAutomationService._device_mode_hint(DeviceModeAPI())
assert "scheduler=no" in hint
assert "запрещает Scheduler" in hint
assert "flagged" in hint

print("CORE TESTS 3.0.8 LV AUTOMATION COMPAT OK")
