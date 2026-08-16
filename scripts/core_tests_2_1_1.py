from __future__ import annotations

import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.services import vpn_automation_service as auto_mod
from linkvideo_vpn_helper.services.vpn_automation_service import (
    LV_AGING_SCHED, LV_ACTIVITY_SCHED, LV_RESTORE_SCHED,
    VPNAutomationService, activity_script_source, aging_script_source,
    restore_script_source,
)
from linkvideo_vpn_helper.services.vpn_lifecycle import (
    ARCHIVE_DAYS, DAY_NS, QUARANTINE_DAYS, SLEEP_DAYS,
    classify_state, compose_lv_comment, parse_lv_comment,
)
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials


# Marker round-trip must preserve operator comments exactly enough for display.
comment = compose_lv_comment("Клиент ПВЗ 15", "Q", 123456789)
meta = parse_lv_comment(comment)
assert meta.base_comment == "Клиент ПВЗ 15"
assert meta.state == "Q"
assert meta.last_ns == 123456789

now = time.time_ns()
assert classify_state(now - (SLEEP_DAYS + 1) * DAY_NS, False) == "S"
assert classify_state(now - (QUARANTINE_DAYS + 1) * DAY_NS, False) == "Q"
assert classify_state(now - (ARCHIVE_DAYS + 1) * DAY_NS, False) == "R"
assert classify_state(0, False) == "U"
assert classify_state(now - 500 * DAY_NS, True) == "M"
assert classify_state(0, True, active=True) == "A"

# Static safety contracts in the RouterOS source.
activity = activity_script_source()
aging = aging_script_source()
restore = restore_script_source()
assert 'service="l2tp"' in activity
assert '($state != "M")' in aging
assert '($last > 0)' in aging
assert 'disabled=yes' in aging
assert '(($state = "Q") || ($state = "R"))' in restore
assert 'disabled=no' in restore
assert 'login failure for user ' in restore
assert auto_mod.LV_LOG_ACTION.isalnum()
assert f'buffer="{auto_mod.LV_LOG_ACTION}"' in restore


class FakeAPI:
    stores: dict[str, dict[str, list[dict]]] = {}
    seq = 0

    def __init__(self, host, username, password, port=8728, timeout=5):
        self.host = host
        self.store = self.stores.setdefault(host, {})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def _rows(self, path):
        return self.store.setdefault(path, [])

    def print(self, path, extra_params=None):
        rows = [dict(x) for x in self._rows(path)]
        if not extra_params:
            return rows
        for key, value in extra_params.items():
            if key.startswith("?") and key.endswith("="):
                field = key[1:-1]
                rows = [r for r in rows if str(r.get(field, "")) == str(value)]
        return rows

    def add(self, path, params):
        type(self).seq += 1
        rid = f"*{type(self).seq}"
        self._rows(path).append({".id": rid, **dict(params)})
        return rid

    def set(self, path, item_id, params):
        for row in self._rows(path):
            if row.get(".id") == item_id:
                row.update(dict(params))
                return
        raise AssertionError(f"missing {path} {item_id}")

    def remove(self, path, item_id):
        self.store[path] = [x for x in self._rows(path) if x.get(".id") != item_id]


real_api = auto_mod.RouterOSAPIClient
auto_mod.RouterOSAPIClient = FakeAPI
try:
    service = VPNAutomationService()
    creds = SessionCredentials("admin", "secret")
    host = "vpn-test.linkvideo.ru"
    status = service.install_or_update(host, creds)
    assert status.installed
    sched = FakeAPI.stores[host]["/system/scheduler"]
    by_name = {x["name"]: x for x in sched}
    assert by_name[LV_ACTIVITY_SCHED]["disabled"] == "no"
    assert by_name[LV_RESTORE_SCHED]["disabled"] == "no"
    assert by_name[LV_ACTIVITY_SCHED].get("start-time") != "startup"
    assert by_name[LV_RESTORE_SCHED].get("start-time") != "startup"
    assert by_name[LV_AGING_SCHED]["disabled"] == "yes", "fresh install must not enable quarantine"

    # Explicit enable must survive an update.
    by_name[LV_AGING_SCHED]["disabled"] = "no"
    status = service.install_or_update(host, creds)
    assert status.aging_enabled

    # Seed is non-destructive: disabled legacy account becomes M, unknown stays U,
    # active L2TP account becomes A. No disabled flag is changed.
    FakeAPI.stores[host]["/ppp/secret"] = [
        {".id": "*s1", "name": "active", "disabled": "no", "last-logged-out": "", "comment": "Объект 1"},
        {".id": "*s2", "name": "manual", "disabled": "yes", "last-logged-out": "jan/01/2025 00:00:00", "comment": ""},
        {".id": "*s3", "name": "unknown", "disabled": "no", "last-logged-out": "", "comment": ""},
    ]
    FakeAPI.stores[host]["/ppp/active"] = [
        {".id": "*a1", "name": "active", "service": "l2tp"},
    ]
    seed = service.seed_lifecycle(host, creds)
    assert seed.total == 3
    assert seed.active == 1 and seed.manual == 1 and seed.unknown == 1
    secrets = {x["name"]: x for x in FakeAPI.stores[host]["/ppp/secret"]}
    assert secrets["manual"]["disabled"] == "yes"
    assert secrets["unknown"]["disabled"] == "no"
    assert parse_lv_comment(secrets["active"]["comment"]).state == "A"
    assert parse_lv_comment(secrets["manual"]["comment"]).state == "M"
    assert parse_lv_comment(secrets["unknown"]["comment"]).state == "U"
finally:
    auto_mod.RouterOSAPIClient = real_api

print("CORE TESTS 2.1.1 VPN AUTOMATION OK")
