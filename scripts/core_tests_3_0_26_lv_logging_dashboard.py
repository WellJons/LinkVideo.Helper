from __future__ import annotations

"""Regression for new MikroTik installs and LV-vs-live status display.

No actual RouterOS is contacted. The fake includes a RouterOS variant that
acknowledges a logging-action add but cannot use it in a logging rule.
"""

from pathlib import Path
from unittest.mock import patch

from linkvideo_vpn_helper.services import vpn_automation_service as public
from linkvideo_vpn_helper.services.vpn_automation_service_core import LV_AGING_SCHED
from linkvideo_vpn_helper.services.vpn_retention_policy import restore_script_source, install_retention_policy
from linkvideo_vpn_helper.services.vpn_automation_resilience import install_vpn_automation_resilience
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
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def print(self, path, params=None):
        if self.mode == "memory_not_listed" and path == "/system/logging/action":
            return [dict(r) for r in self.rows[path] if r.get("name") != "memory"]
        if self.mode == "memory_no_id" and path == "/system/logging/action":
            return [dict(r) for r in self.rows[path] if r.get("name") != "LVAuth"]
        if self.mode == "memory_no_id" and path == "/system/logging":
            return [{k: v for k, v in r.items() if k != ".id"} for r in self.rows[path]]
        return [dict(r) for r in self.rows[path]]

    def talk(self, command, params=None, raise_on_trap=True):
        self.commands.append(command)
        if self.mode == "find_ignores_name" and command in {
            "/system/script/find", "/system/scheduler/find",
        }:
            # An improperly filtered /find returns an unrelated first ID.
            # Correct Helper code must use /print query + exact name check.
            path = command[:-5]
            rows = self.rows.get(path, [])
            return [{"ret": rows[0][".id"]}] if rows else []
        return []

    def add(self, path, params):
        self.mutations.append(("add", path, dict(params)))
        if path == "/system/logging/action" and params["name"] == "LVAuth":
            if self.mode in {"reject_create", "memory_not_listed", "memory_no_id"}:
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
        if self.mode == "memory_no_id" and path == "/system/logging":
            return ""
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

    def remove(self, path, rid):
        self.mutations.append(("remove", path, {".id": rid}))
        self.rows[path] = [
            row for row in self.rows[path]
            if str(row.get(".id", "") or "") != str(rid)
        ]

    def enable(self, path, rid):
        self.set(path, rid, {"disabled": "no"})

    def disable(self, path, rid):
        self.set(path, rid, {"disabled": "yes"})


# Run the same startup monkeypatch order as the real desktop application.
# The previous regression tested only the base class and missed the broken
# resilience wrapper that silently restored /find at application startup.
install_vpn_automation_resilience()
install_retention_policy()
svc = public.VPNAutomationService()
creds = SessionCredentials("fake-operator", "fake-password")
for mode, expected_action in (
    ("normal", "LVAuth"),
    ("reject_create", "memory"),
    ("ack_without_action", "memory"),
    ("reject_reference", "memory"),
    ("reject_extended_args", "LVAuth"),
    ("memory_not_listed", "memory"),
    ("memory_no_id", "memory"),
    ("find_ignores_name", "LVAuth"),
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
    assert {row["name"] for row in api.rows["/system/script"]} == {
        "LV-Activity", "LV-Aging", "LV-AutoRestore",
    }, (mode, api.rows["/system/script"])
    assert {row["name"] for row in api.rows["/system/scheduler"]} == {
        "LV-Activity", "LV-Aging", "LV-AutoRestore",
    }, (mode, api.rows["/system/scheduler"])
    assert next(s for s in api.rows["/system/scheduler"] if s["name"] == LV_AGING_SCHED)["disabled"] == "yes"
    assert not any(path.startswith("/ppp") for op, path, params in api.mutations)
    assert not any("/find" in cmd for cmd in getattr(api, "commands", [])), mode

    if expected_action == "memory" and mode != "memory_no_id":
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


# Actual vpn02 failure state: one overwritten LV script and one scheduler
# remain after /find returned the same incorrect ID for all three names.
api = FakeRouterOS("find_ignores_name")
api.rows["/system/script"] = [{
    ".id": "*oldscript", "name": "LV-Activity",
    "source": "BROKEN_OLD_SOURCE", "comment": "incomplete",
}]
api.rows["/system/scheduler"] = [{
    ".id": "*oldsched", "name": "LV-Activity", "on-event": "LV-AutoRestore",
    "disabled": "yes",
}]
api.rows["/system/script"].append({
    ".id": "*unrelatedscript", "name": "Unrelated-Customer-Script",
    "source": "customer code",
})
api.rows["/system/scheduler"].append({
    ".id": "*unrelatedsched", "name": "Unrelated-Customer-Scheduler",
    "disabled": "no",
})
svc._ensure_components(api)
assert {r["name"] for r in api.rows["/system/script"] if r["name"].startswith("LV-")} == {
    "LV-Activity", "LV-Aging", "LV-AutoRestore"
}
assert {r["name"] for r in api.rows["/system/scheduler"] if r["name"].startswith("LV-")} == {
    "LV-Activity", "LV-Aging", "LV-AutoRestore"
}
assert next(row for row in api.rows["/system/script"]
            if row["name"] == "LV-Activity")["source"] != "BROKEN_OLD_SOURCE"
assert next(row for row in api.rows["/system/scheduler"]
            if row["name"] == "LV-Activity")["on-event"] == "LV-Activity"
assert next(row for row in api.rows["/system/script"]
            if row["name"] == "Unrelated-Customer-Script")["source"] == "customer code"
assert next(row for row in api.rows["/system/scheduler"]
            if row["name"] == "Unrelated-Customer-Scheduler")["disabled"] == "no"
assert not any(op == "remove" and path in {"/system/script", "/system/scheduler"}
               for op, path, _ in api.mutations)


# A crashed previous install may have created duplicate LV rules, but the
# recovery removes ONLY exact LV-owned duplicates and keeps stock logging.
api = FakeRouterOS("normal")
svc._ensure_components(api)
original = dict(api.rows["/system/logging"][0])
extra = dict(next(r for r in api.rows["/system/logging"]
                  if r.get("topics") == "ppp" and r.get("action") == "LVAuth"))
extra[".id"] = "*extra-lv"
api.rows["/system/logging"].append(extra)
foreign = {".id": "*foreign", "topics": "ppp", "action": "memory", "prefix": "OTHER"}
api.rows["/system/logging"].append(foreign)
svc._ensure_components(api)
assert api.rows["/system/logging"][0] == original
assert foreign in api.rows["/system/logging"]
assert len([r for r in api.rows["/system/logging"] if r.get("topics") == "ppp"
            and r.get("action") == "LVAuth"]) == 1
assert not any(path == "/ppp/secret" for _, path, _ in api.mutations)
assert not any(path == "/ip/firewall/nat" for _, path, _ in api.mutations)

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
