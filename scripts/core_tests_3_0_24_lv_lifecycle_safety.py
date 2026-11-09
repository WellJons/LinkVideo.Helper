from __future__ import annotations

"""Regression tests for LV2.1 lifecycle compact metadata and non-destructive rollout.

Offline only: no live RouterOS, PPP account, NAT rule or Google Sheet is touched.
"""
import re
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from linkvideo_vpn_helper.services.vpn_lifecycle import (
    LV_AUTOMATION_VERSION, LV_MARKER, DAY_NS,
    parse_lv_comment, compose_lv_comment, classify_state,
)
from linkvideo_vpn_helper.services.vpn_automation_service import VPNAutomationService
from linkvideo_vpn_helper.services.vpn_automation_service_core import (
    activity_script_source, aging_script_source, restore_script_source,
)
from linkvideo_vpn_helper.services.vpn_backup_service import VPNServerBackupResult

assert LV_AUTOMATION_VERSION == "2.1.0"

now_ns = time.time_ns()
day = now_ns // DAY_NS
old_ns = (day - 95) * DAY_NS
orig = "Абонент: камера 12 · оставить заметку техника"

# 2.0.0 production RouterOS mistakenly wrote verbose LV2 values.
verbose = f"{orig} |LV2|state=Q|last={old_ns}|ver=2.0.0|"
parsed = parse_lv_comment(verbose)
assert parsed.base_comment == orig
assert parsed.state == "Q"
assert parsed.last_ns == old_ns
assert parsed.version == "2.0.0"

# Legacy LV1 from historical installs remains readable.
legacy = f"{orig} |LV1|state=S|last={old_ns}|ver=1.0.0|"
parsed_old = parse_lv_comment(legacy)
assert parsed_old.state == "S"
assert parsed_old.base_comment == orig

compact = compose_lv_comment(orig, "Q", old_ns)
assert compact == f"{orig} |LV2|s=Q|l={day-95}|"
assert len(compact) - len(orig) < 25
again = compose_lv_comment(parse_lv_comment(compact).base_comment, "Q", old_ns)
assert again == compact  # no duplicated tags
assert parse_lv_comment(compact).state == "Q"
assert parse_lv_comment(compact).last_ns == old_ns
assert parse_lv_comment(compact).version == "2.1.0"

# Unknown accounts preserve an operator's original comment untouched.
assert compose_lv_comment(orig, "U", 0) == orig
assert compose_lv_comment("", "U", 0) == ""

# Explicitly manual disabled state is preserved even with no known last activity.
manually_disabled = compose_lv_comment(orig, "M", 0)
assert "s=M" in manually_disabled
assert parse_lv_comment(manually_disabled).state == "M"

# Migration is non-destructive for operator notes and both old marker variants.
for input_comment in (legacy, verbose, compact):
    old = parse_lv_comment(input_comment)
    result = compose_lv_comment(old.base_comment, old.state, old.last_ns)
    assert result.startswith(orig + " ")
    assert result.count("|LV2|") == 1
    assert "|LV1|" not in result
    assert "|state=" not in result
    assert "|last=" not in result

# Real inactivity thresholds still use 30/90/365 days. No metadata must
# yield U (unknown) rather than destructive archive/quarantine guesses.
assert classify_state(0, False) == "U"
assert classify_state((day - 31) * DAY_NS, False) == "S"
assert classify_state((day - 91) * DAY_NS, False) == "Q"
assert classify_state((day - 366) * DAY_NS, False) == "R"
assert classify_state((day - 366) * DAY_NS, True) == "M"
assert classify_state(0, False, active=True) == "A"

for source in (activity_script_source(), aging_script_source(), restore_script_source()):
    assert "LV Automation 2.1.0" in source
    assert "|LV2|s=" in source, "all scripts must write/read compact state"
    assert "|l=" in source, "all scripts must write/read compact day timestamp"
    assert "|state=" in source, "legacy verbose metadata must still be readable"
    assert "|last=" in source, "legacy nanosecond metadata must still be readable"
    assert "/ppp secret remove" not in source
    assert "/ip firewall nat remove" not in source
    assert "ver=2.0.0" not in source
    assert source.count("{") == source.count("}"), "unbalanced RouterOS blocks"

activity = activity_script_source()
assert 'service="l2tp"' in activity
assert '($lastDay != $nowDay)' in activity, "avoid repeated writes every 10m"
aging = aging_script_source()
assert '$state != "M"' in aging
assert '$lastDay > 0' in aging
assert '($desired = "Q") || ($desired = "R")' in aging
assert "/ppp secret set $sid disabled=yes" in aging
assert "/ppp secret remove" not in aging
restore = restore_script_source()
assert '($state = "Q") || ($state = "R")' in restore
assert "$state = \"M\"" not in restore, "manual state may never be auto-restored"

# New installation is non-destructive by default: LV-Aging stays off.
class FakeRouterOS:
    def __init__(self):
        self.rows = {
            "/system/script": [], "/system/scheduler": [],
            "/system/logging/action": [], "/system/logging": [],
        }
        self.next_id = 1

    def print(self, path, params=None):
        return [dict(x) for x in self.rows.setdefault(path, [])]

    def talk(self, command, params=None, raise_on_trap=True):
        return []

    def add(self, path, params):
        ident = "*" + str(self.next_id)
        self.next_id += 1
        self.rows[path].append({".id": ident, **params})
        return ident

    def set(self, path, ident, params):
        for row in self.rows[path]:
            if row.get(".id") == ident:
                row.update(params)
                return
        raise KeyError((path, ident))

    def disable(self, path, ident):
        self.set(path, ident, {"disabled": "yes"})

    def enable(self, path, ident):
        self.set(path, ident, {"disabled": "no"})

api = FakeRouterOS()
service = VPNAutomationService()
service._ensure_components(api, preserve_pause=False)
schedulers = {x["name"]: x for x in api.rows["/system/scheduler"]}
assert schedulers["LV-Activity"]["disabled"] == "no"
assert schedulers["LV-AutoRestore"]["disabled"] == "no"
assert schedulers["LV-Aging"]["disabled"] == "yes"
assert "2.1.0" in next(x["source"] for x in api.rows["/system/script"] if x["name"] == "LV-Activity")
service._ensure_components(api, preserve_pause=True)
assert len(api.rows["/system/script"]) == 3
assert len(api.rows["/system/scheduler"]) == 3
assert len(api.rows["/system/logging/action"]) == 1
assert len(api.rows["/system/logging"]) == 2

# A saved JSON does not prove recovery completeness.
with TemporaryDirectory() as temp:
    path = Path(temp) / "backup.json"
    path.write_text('{"sections":{}}', encoding="utf-8")
    good = VPNServerBackupResult("vpn.example", json_path=path)
    assert good.ok
    partial = VPNServerBackupResult(
        "vpn.example", json_path=path, errors=["/ip/firewall/nat: API timeout"]
    )
    assert not partial.ok
    no_ppp = VPNServerBackupResult(
        "vpn.example", json_path=path, errors=["/ppp/secret: permission denied"]
    )
    assert not no_ppp.ok
    optional = VPNServerBackupResult(
        "vpn.example", json_path=path, errors=["/system/resource: unknown parameter"]
    )
    assert optional.ok

ui = (Path(__file__).resolve().parents[1] /
      "linkvideo_vpn_helper/ui/vpn_servers_status_ui.py").read_text(encoding="utf-8")
assert "Непосредственно на RouterOS LV-Aging только помечает" in ui
assert "Полное удаление выполняется после успешного копирования в Google Sheets" in ui

print("CORE TESTS 3.0.24 LV2.1 COMPACT COMMENT, MIGRATION AND SAFE QUARANTINE OK")
