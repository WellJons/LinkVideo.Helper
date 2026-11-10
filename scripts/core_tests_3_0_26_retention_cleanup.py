from __future__ import annotations

"""Offline regression: 30/90/365 retention without Google Sheets dependency.

No RouterOS connection, real Google spreadsheet, or live VPN resource is touched.
"""

import json
import time
from datetime import datetime
from unittest.mock import patch

from linkvideo_vpn_helper.services import vpn_retention_policy as policy
from linkvideo_vpn_helper.services.vpn_sheets_retention_compat import _retention_overdue
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials


DAY_NS = policy.DAY_NS
NOW_DAY = time.time_ns() // DAY_NS
CREDS = SessionCredentials("test", "offline")


def timestamp(days_ago: int) -> int:
    return (NOW_DAY - days_ago) * DAY_NS


def secret(login: str, *, last: int = 0, created: int | None = None, remote: str = "172.16.0.3",
           profile: str = "unique", state: str = "U", disabled: str = "no", rid: str = "*1") -> dict:
    created_ns = timestamp(40) if created is None else created
    return {
        ".id": rid,
        "name": login,
        "profile": profile,
        "remote-address": remote,
        "disabled": disabled,
        "comment": policy.compose_extended_comment("test", state, last, created_ns, "created"),
        "last-logged-out": "",
    }


def nat_rule(rid: str, login: str, remote: str) -> dict:
    return {
        ".id": rid, "comment": login, "chain": "dstnat", "action": "dst-nat",
        "to-addresses": remote, "dst-port": "10001",
    }


class FakeAPI:
    def __init__(self, secrets: list[dict], profiles: list[dict], nat: list[dict], active=()):
        self.rows = {
            "/ppp/secret": [dict(row) for row in secrets],
            "/ppp/profile": [dict(row) for row in profiles],
            "/ip/firewall/nat": [dict(row) for row in nat],
            "/ppp/active": [{"name": name, "service": "l2tp"} for name in active],
        }
        self.removed: list[tuple[str, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def print(self, path, params=None):
        return [dict(row) for row in self.rows[path]]

    def set(self, path, rid, values):
        row = next(x for x in self.rows[path] if x.get(".id") == rid)
        row.update(values)

    def disable(self, path, rid):
        self.set(path, rid, {"disabled": "yes"})

    def remove(self, path, rid):
        self.rows[path] = [row for row in self.rows[path] if row.get(".id") != rid]
        self.removed.append((path, rid))


def run(api: FakeAPI):
    with patch.object(policy, "RouterOSAPIClient", lambda *args, **kwargs: api):
        with patch.object(policy, "event", lambda *args, **kwargs: None):
            return policy.apply_policy_now("offline.test", CREDS)


def sheet_row(entry: dict, *, snapshot: bool = True) -> dict:
    return {
        "Логин": entry["name"],
        "Комментарий RouterOS": entry["comment"],
        "RouterOS snapshot": json.dumps({"secret": entry, "profile": {}, "nat_rules": []}) if snapshot else "",
    }


assert policy.RETENTION_VERSION == "2.1.2"
assert policy.NEVER_ACTIVE_DELETE_DAYS == 30
assert policy.SLEEP_DAYS == 30
assert policy.QUARANTINE_DAYS == 90
assert policy.DELETE_DAYS == 365

script = policy.aging_script_source()
assert "/ppp secret remove $sid" in script
assert "/ip firewall nat remove $nid" in script
assert script.count("{") == script.count("}")
assert "LV RETENTION DELETE" in script
assert ':set deleteAfter 30' in script
assert ':local deleteAfter 365' in script

# A 366-day inactive client is deleted even if Google Sheets is unavailable.
expired = secret("expired", last=timestamp(366), created=timestamp(500))
api = FakeAPI(
    [expired],
    [{".id": "*p", "name": "unique", "remote-address": "172.16.0.3"}],
    [nat_rule("*n", "expired", "172.16.0.3")],
)
result = run(api)
assert result["deleted"] == 1 and result["nat_removed"] == 1, result
assert not api.rows["/ppp/secret"]
assert not api.rows["/ip/firewall/nat"]

# A verified never-connected client is deleted at 30 days without Sheets.
never = secret("never", last=0, created=timestamp(31))
api = FakeAPI(
    [never],
    [{".id": "*p", "name": "unique", "remote-address": "172.16.0.3"}],
    [nat_rule("*n", "never", "172.16.0.3")],
)
result = run(api)
assert result["deleted"] == 1 and result["nat_removed"] == 1
assert result["profiles_removed"] == 1
assert not api.rows["/ppp/secret"] and not api.rows["/ip/firewall/nat"]
assert not api.rows["/ppp/profile"]

# 29-day never-connected, and 364-day previously used, must survive.
young = secret("young", last=0, created=timestamp(29), rid="*1")
aging = secret("aging", last=timestamp(364), created=timestamp(600),
               remote="172.16.0.4", rid="*2")
api = FakeAPI([young, aging], [], [])
result = run(api)
assert result["deleted"] == 0
assert len(api.rows["/ppp/secret"]) == 2

# Shared remote/profile: only the expired client's explicitly owned NAT rule is removed.
old = secret("old", last=timestamp(366), created=timestamp(400), remote="172.16.0.8",
             profile="shared", rid="*1")
other = secret("other", last=timestamp(1), created=timestamp(100), remote="172.16.0.8",
               profile="shared", rid="*2")
api = FakeAPI(
    [old, other], [{".id": "*p", "name": "shared", "remote-address": "172.16.0.8"}],
    [
        nat_rule("*n1", "old", "172.16.0.8"),
        nat_rule("*n2", "other", "172.16.0.8"),
        nat_rule("*n3", "", "172.16.0.8"),
        nat_rule("*n4", "old", "172.16.99.99"),
    ],
)
result = run(api)
assert result["deleted"] == 1 and result["nat_removed"] == 1, result
assert [r["name"] for r in api.rows["/ppp/secret"]] == ["other"]
assert [r[".id"] for r in api.rows["/ip/firewall/nat"]] == ["*n2", "*n3", "*n4"]
assert [r["name"] for r in api.rows["/ppp/profile"]] == ["shared"]

# Active sessions and unknown/uninitialized creation are not deletion candidates.
connected = secret("connected", last=timestamp(366), created=timestamp(500))
unknown = {**secret("unknown", last=0, created=0, rid="*2"), "comment": ""}
api = FakeAPI([connected, unknown], [], [], active=("connected",))
result = run(api)
assert result["deleted"] == 0
assert len(api.rows["/ppp/secret"]) == 2

# The mirror may approve only a login with both dated LV metadata and a real snapshot.
rows = [
    sheet_row(secret("old", last=timestamp(366), created=timestamp(400))),
    sheet_row(secret("recent", last=timestamp(364), created=timestamp(400))),
    sheet_row(secret("never", last=0, created=timestamp(31))),
    sheet_row(secret("new", last=0, created=timestamp(29))),
    sheet_row(secret("no-backup", last=0, created=timestamp(31)), snapshot=False),
    sheet_row({**secret("legacy", last=0, created=timestamp(60)),
               "comment": policy.compose_extended_comment("", "U", 0, timestamp(60), "never_active_tracking")}),
    {"Логин": "unknown", "Комментарий RouterOS": "", "Первое обнаружение": "2020-01-01",
     "RouterOS snapshot": json.dumps({"secret": {"name": "unknown"}})},
]
assert set(_retention_overdue(rows, datetime.now())) == {"old", "never"}

print("CORE TESTS 3.0.26 RETENTION 30/90/365 + DIRECT CLEANUP + ACTIVE CLIENT SAFETY OK")
