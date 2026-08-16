from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.services.vpn_retention_policy import (
    RETENTION_VERSION,
    aging_script_source,
    compose_extended_comment,
    install_retention_policy,
    parse_extended_comment,
    restore_script_source,
)
from linkvideo_vpn_helper.services.vpn_sheets_retention_compat import install_vpn_sheets_retention_compat


assert RETENTION_VERSION.startswith("2."), RETENTION_VERSION
DAY_NS = 86_400_000_000_000
comment = compose_extended_comment("legacy note", "Q", 111 * DAY_NS, 99 * DAY_NS, "inactive_90")
parsed = parse_extended_comment(comment)
assert parsed.base_comment == "legacy note"
assert parsed.state == "Q"
assert parsed.last_ns == 111 * DAY_NS
assert parsed.created_ns == 99 * DAY_NS
assert parsed.reason == "inactive_90"
assert parsed.version == RETENTION_VERSION
assert "|LV2|s=Q|l=111|c=99|r=q|" in comment
assert "state=" not in comment and "created=" not in comment

legacy = "operator |LV1|state=Q|last=9590400000000000|created=8553600000000000|reason=inactive_90|ver=1.1.0|"
migrated = parse_extended_comment(legacy)
assert migrated.base_comment == "operator"
assert migrated.state == "Q"
assert migrated.last_ns == 9590400000000000
assert migrated.created_ns == 8553600000000000
assert migrated.reason == "inactive_90"

aging = aging_script_source()
for marker in (
    "never_active_365",
    "inactive_365",
    "/ppp secret disable $sid",
    "/ppp secret remove $sid",
    "/ip firewall nat remove $nid",
    "/ppp profile remove $pid",
    "LV QUARANTINE",
    "LV RETENTION DELETE",
    "|c=",
):
    assert marker in aging, marker

restore = restore_script_source()
assert '($state = "Q")' in restore
assert '($state = "R")' not in restore
assert "/ppp secret enable $sid" in restore
assert "LV RESTORE" in restore

install_retention_policy()
from linkvideo_vpn_helper.services import vpn_automation_service as automation
from linkvideo_vpn_helper.services import vpn_automation_service_core as automation_core
from linkvideo_vpn_helper.services import vpn_lifecycle

assert automation.LV_AUTOMATION_VERSION == RETENTION_VERSION
assert automation_core.LV_AUTOMATION_VERSION == RETENTION_VERSION
assert vpn_lifecycle.LV_AUTOMATION_VERSION == RETENTION_VERSION
assert "never_active_365" in automation.VPNAutomationService.SCRIPT_SOURCES[automation_core.LV_AGING_SCRIPT]()
assert "|LV2|" in automation.VPNAutomationService.SCRIPT_SOURCES[automation_core.LV_ACTIVITY_SCRIPT]()

install_vpn_sheets_retention_compat()
from linkvideo_vpn_helper.services import vpn_sheets_sync as sheets

assert sheets.SERVER_COLUMNS[-1] == "Причина"
assert "Причина" in sheets.COMPARE_COLUMNS
assert len(sheets.SERVER_COLUMNS) == 20

sheet_compat = (ROOT / "linkvideo_vpn_helper/services/vpn_sheets_retention_compat.py").read_text(encoding="utf-8")
assert "A2:T1500" in sheet_compat
assert "A2:T{write_count + 1}" in sheet_compat
assert "Удалена автоматически" in sheet_compat
assert "Отключена автоматически: нет активности 90+ дней" in sheet_compat

manual_refresh = (ROOT / "linkvideo_vpn_helper/ui/vpn_servers_manual_refresh.py").read_text(encoding="utf-8")
assert "self.refresh()" not in manual_refresh
assert "Обновить данные" in manual_refresh

print("CORE TESTS VPN RETENTION POLICY LV2 OK")
