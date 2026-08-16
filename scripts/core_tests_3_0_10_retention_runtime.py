from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import linkvideo_vpn_helper.services.vpn_retention_policy as policy
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials


DAY_NS = policy.DAY_NS
NOW_NS = time.time_ns()


def old(days: int) -> int:
    return NOW_NS - days * DAY_NS


class FakeRouterOSAPIClient:
    secrets = [
        {
            ".id": "*Q100", "name": "quarantine100", "profile": "p-q", "remote-address": "10.0.0.10",
            "disabled": "no", "last-logged-out": "", "comment": policy.compose_extended_comment("", "A", old(100), old(300), "tracked"),
        },
        {
            ".id": "*DEL370", "name": "delete370", "profile": "p-del", "remote-address": "10.0.0.20",
            "disabled": "yes", "last-logged-out": "", "comment": policy.compose_extended_comment("", "M", old(370), old(500), "manual_disabled"),
        },
        {
            ".id": "*NEVER", "name": "never366", "profile": "p-never", "remote-address": "10.0.0.30",
            "disabled": "no", "last-logged-out": "", "comment": policy.compose_extended_comment("", "U", 0, old(366), "never_active_tracking"),
        },
        {
            ".id": "*MAN100", "name": "manual100", "profile": "p-man", "remote-address": "10.0.0.40",
            "disabled": "yes", "last-logged-out": "", "comment": policy.compose_extended_comment("", "M", old(100), old(200), "manual_disabled"),
        },
        {
            ".id": "*ACTIVE", "name": "online", "profile": "p-on", "remote-address": "10.0.0.50",
            "disabled": "no", "last-logged-out": "", "comment": policy.compose_extended_comment("", "S", old(45), old(600), "inactive_30"),
        },
    ]
    profiles = [
        {".id": "*PQ", "name": "p-q", "remote-address": "10.0.0.10"},
        {".id": "*PD", "name": "p-del", "remote-address": "10.0.0.20"},
        {".id": "*PN", "name": "p-never", "remote-address": "10.0.0.30"},
        {".id": "*PM", "name": "p-man", "remote-address": "10.0.0.40"},
        {".id": "*PO", "name": "p-on", "remote-address": "10.0.0.50"},
    ]
    nat_rules = [
        {".id": "*NQ", "comment": "quarantine100", "to-addresses": "10.0.0.10"},
        {".id": "*ND1", "comment": "delete370", "to-addresses": "10.0.0.20"},
        {".id": "*ND2", "comment": "", "to-addresses": "10.0.0.20"},
        {".id": "*NN", "comment": "never366", "to-addresses": "10.0.0.30"},
    ]
    actives = [{".id": "*A1", "name": "online", "service": "l2tp"}]

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    @staticmethod
    def _copy(rows):
        return [dict(row) for row in rows]

    def print(self, path: str, params=None):
        if path == "/ppp/secret":
            return self._copy(self.secrets)
        if path == "/ppp/profile":
            return self._copy(self.profiles)
        if path == "/ip/firewall/nat":
            return self._copy(self.nat_rules)
        if path == "/ppp/active":
            return self._copy(self.actives)
        return []

    def _row(self, rows, rid):
        return next(row for row in rows if row.get(".id") == rid)

    def set(self, path: str, rid: str, params: dict):
        rows = self.secrets if path == "/ppp/secret" else self.profiles
        self._row(rows, rid).update(dict(params))

    def disable(self, path: str, rid: str):
        assert path == "/ppp/secret"
        self._row(self.secrets, rid)["disabled"] = "yes"

    def enable(self, path: str, rid: str):
        assert path == "/ppp/secret"
        self._row(self.secrets, rid)["disabled"] = "no"

    def remove(self, path: str, rid: str):
        rows = {
            "/ppp/secret": self.secrets,
            "/ppp/profile": self.profiles,
            "/ip/firewall/nat": self.nat_rules,
        }[path]
        rows[:] = [row for row in rows if row.get(".id") != rid]


# Keep each preflight invocation isolated even if Python ever reuses the module.
FakeRouterOSAPIClient.secrets = FakeRouterOSAPIClient._copy(FakeRouterOSAPIClient.secrets)
FakeRouterOSAPIClient.profiles = FakeRouterOSAPIClient._copy(FakeRouterOSAPIClient.profiles)
FakeRouterOSAPIClient.nat_rules = FakeRouterOSAPIClient._copy(FakeRouterOSAPIClient.nat_rules)
FakeRouterOSAPIClient.actives = FakeRouterOSAPIClient._copy(FakeRouterOSAPIClient.actives)
policy.RouterOSAPIClient = FakeRouterOSAPIClient

creds = SessionCredentials("test", "test")
result = policy.apply_policy_now("vpn-test", creds)

by_name = {row["name"]: row for row in FakeRouterOSAPIClient.secrets}
assert "delete370" not in by_name
assert "never366" not in by_name
assert "p-del" not in {row["name"] for row in FakeRouterOSAPIClient.profiles}
assert "p-never" not in {row["name"] for row in FakeRouterOSAPIClient.profiles}
assert not any(row.get("to-addresses") == "10.0.0.20" for row in FakeRouterOSAPIClient.nat_rules)
assert not any(row.get("to-addresses") == "10.0.0.30" for row in FakeRouterOSAPIClient.nat_rules)

q = by_name["quarantine100"]
qmeta = policy.parse_extended_comment(q["comment"])
assert q["disabled"] == "yes"
assert qmeta.state == "Q" and qmeta.reason == "inactive_90"

manual = by_name["manual100"]
manual_meta = policy.parse_extended_comment(manual["comment"])
assert manual["disabled"] == "yes"
assert manual_meta.state == "M"

online = by_name["online"]
online_meta = policy.parse_extended_comment(online["comment"])
assert online["disabled"] == "no"
assert online_meta.state == "A"
assert online_meta.reason == "activity"

assert result["deleted"] == 2, result
assert result["profiles_removed"] == 2, result
assert result["nat_removed"] == 3, result
assert result["quarantined"] >= 1, result

compact = policy.compose_extended_comment("", "Q", old(100), old(500), "inactive_90")
assert compact.startswith("|LV2|")
assert len(compact) < 60, compact
assert "state=" not in compact and "created=" not in compact and "reason=" not in compact

# Legacy LV1 must remain readable so 3.0.10 upgrades do not lose timestamps.
legacy = (
    f"note |LV1|state=Q|last={old(100)}|created={old(500)}|"
    "reason=inactive_90|ver=1.1.0|"
)
legacy_meta = policy.parse_extended_comment(legacy)
assert legacy_meta.base_comment == "note"
assert legacy_meta.state == "Q"
assert legacy_meta.reason == "inactive_90"
assert legacy_meta.last_ns > 0 and legacy_meta.created_ns > 0

print("CORE TESTS 3.0.10 RETENTION RUNTIME OK")
