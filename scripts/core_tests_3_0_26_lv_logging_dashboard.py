from __future__ import annotations

"""Regression for new MikroTik installs and LV-vs-live status display.

No actual RouterOS is contacted. The fake includes a RouterOS variant that
acknowledges a logging-action add but cannot use it in a logging rule.
"""

from pathlib import Path
from unittest.mock import patch

from linkvideo_vpn_helper.services import vpn_automation_service as public
from linkvideo_vpn_helper.services.vpn_automation_service_core import LV_AGING_SCHED
from linkvideo_vpn_helper.services.vpn_retention_policy import restore_script_source
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials


class FakeRouterOS:
    def __init__(self, mode: str):
        self.mode = mode
        self._next_id = 1
        self.rows = {
            "/system/script": [],
            "/system/scheduler": [],
            "/system/logging/action": [
                {".id": "*m", "name": "memory", "target": "memory"},
                {".id": "*d", "name": "disk", "target": "disk"},
            ],
            "/system/logging": [
                {".id": "*default", "topics": "info", "action": "memory", "prefix": ""},
            ],
            "/ppp/secret": [
                {".id": "*u", "name": "someone", "comment": "", "disabled": "no"}
            ],
        }
        self.mutations = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def print(self, path, params=None):
        return [dict(r) for r in self.rows[path]]

    def talk(self, command, params=None, raise_on_trap=True):
        return []

    def add(self, path, params):
        self.mutations.append(("add", path, dict(params)))
        if path == "/system/logging/action" and params["name"] == "LVAuth":
            if self.mode == "reject_create":
                raise RuntimeError("input does not match any value of action")
            if self.mode == "ack_without_action":
                return ""
        if path == "/system/logging" and self.mode == "reject_extended_args":
            assert set(params) == {"topics", "action"}, "No extra logging params in add"
        if path == "/system/logging" and params.get("action") == "LVAuth":
            if self.mode == "reject_reference":
                raise RuntimeError("input does not match any value of action")
            assert any(r.get("name") == "LVAuth" for r in self.rows["/system/logging/action"])
        rid = "*" + str(self._next_id)
        self._next_id += 1
        self.rows[path].append({".id": rid, **params})
        return rid

    def set(self, path, rid, params):
        self.mutations.append(("set", path, dict(params)))
        if path == "/system/logging" and params.get("action") == "LVAuth" and self.mode == "reject_reference":
            raise RuntimeError("input does not match any value of action")
        if path == "/system/logging" and self.mode == "reject_extended_args":
            if "regex" in params:
                raise RuntimeError("unknown parameter")
            assert not ({"topics", "action"} <= set(params)), "No combined updates"
        row = next(r for r in self.rows[path] if r.get(".id") == rid)
        row.update(params)

    def enable(self, path, rid):
        self.set(path, rid, {"disabled": "no"})

    def disable(self, path, rid):
        self.set(path, rid, {"disabled": "yes"})


svc = public.VPNAutomationService()
creds = SessionCredentials("fake-operator", "fake-password")
for mode, expected_action in (
    ("normal", "LVAuth"),
    ("reject_create", "memory"),
    ("ack_without_action", "memory"),
    ("reject_reference", "memory"),
    ("reject_extended_args", "LVAuth"),
):
    api = FakeRouterOS(mode)
    # The new server has an unrelated global default memory rule.
    original_default = dict(api.rows["/system/logging"][0])

    svc._ensure_components(api)
    actions = api.rows["/system/logging/action"]
    rules = api.rows["/system/logging"]
    managed = [
        r for r in rules
        if r.get("topics") in ("ppp", "l2tp")
        and r.get("action") == expected_action
    ]
    assert len(managed) == 2, (mode, rules)
    assert rules[0] == original_default, "Never change unrelated logging rules"
    assert len(api.rows["/system/script"]) == 3
    assert len(api.rows["/system/scheduler"]) == 3
    assert next(s for s in api.rows["/system/scheduler"] if s["name"] == LV_AGING_SCHED)["disabled"] == "yes"
    assert not any(path.startswith("/ppp") for op, path, params in api.mutations)

    if expected_action == "memory":
        assert {r.get("prefix") for r in managed} == {"LV-AUTH-PPP", "LV-AUTH-L2TP"}

    # A second install must repair, not append duplicate LV objects.
    svc._ensure_components(api)
    assert len(api.rows["/system/logging"]) == 3, (mode, api.rows["/system/logging"])
    assert len(api.rows["/system/script"]) == 3
    assert len(api.rows["/system/scheduler"]) == 3

    with patch.object(public, "RouterOSAPIClient", lambda *args, **kwargs: api):
        status = svc.get_status("mock.example", creds)
    assert status.logging_ready, (mode, status.installation_detail)
    assert status.installed, (mode, status.installation_detail)
    assert status.unknown == 1
    assert status.active == 0  # no LV marker yet, not an assertion of zero L2TP online

script = restore_script_source()
assert ':local lvBuffer "LVAuth"' in script
assert ':set lvBuffer "memory"' in script
assert "/log find where buffer=$lvBuffer" in script

page = (Path(__file__).resolve().parents[1] /
        "linkvideo_vpn_helper/ui/pages/vpn_servers_page.py").read_text(encoding="utf-8")
assert '("L2TP онлайн", online_value)' in page
assert '("Спящие LV", state_value("sleeping"))' in page
assert '("Без LV-истории", state_value("unknown"))' in page
assert 'lv_states_ready = bool(auto and auto.installed and auto.initialized > 0)' in page
assert '"—"' in page

print("LV LOGGING FALLBACK + INDEPENDENT LIVE L2TP STATUS OK")
