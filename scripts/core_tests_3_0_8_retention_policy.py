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


assert RETENTION_VERSION == "1.1.0"
comment = compose_extended_comment("legacy note", "Q", 111, 99, "inactive_90")
parsed = parse_extended_comment(comment)
assert parsed.base_comment == "legacy note"
assert parsed.state == "Q"
assert parsed.last_ns == 111
assert parsed.created_ns == 99
assert parsed.reason == "inactive_90"
assert parsed.version == RETENTION_VERSION

aging = aging_script_source()
for marker in (
    "deleteNs",
    "never_active_365",
    "inactive_365",
    "/ppp secret remove $sid",
    "/ip firewall nat remove $nid",
    "reason=inactive_90",
    "created=",
    "LV RETENTION DELETE",
):
    assert marker in aging, marker

restore = restore_script_source()
assert '($state = "Q")' in restore
assert '($state = "R")' not in restore
assert "reason=auto_restore" in restore

install_retention_policy()
from linkvideo_vpn_helper.services import vpn_automation_service as automation
from linkvideo_vpn_helper.services import vpn_automation_service_core as automation_core
from linkvideo_vpn_helper.services import vpn_lifecycle

assert automation.LV_AUTOMATION_VERSION == RETENTION_VERSION
assert automation_core.LV_AUTOMATION_VERSION == RETENTION_VERSION
assert vpn_lifecycle.LV_AUTOMATION_VERSION == RETENTION_VERSION
assert "never_active_365" in automation.VPNAutomationService.SCRIPT_SOURCES[automation_core.LV_AGING_SCRIPT]()

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

print("CORE TESTS 3.0.8 VPN RETENTION POLICY OK")
