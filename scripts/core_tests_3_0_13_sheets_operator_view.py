from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.services.vpn_sheets_operator_view import (
    _compact_ports,
    _describe_port_change,
    _normalize_visible_row,
)


assert _compact_ports(
    "tcp 10001→10001; tcp 10002→10002; udp 10003→20003; tcp 10004→10004 [off]"
) == "10001; 10002; 10003; 10004 [off]"
assert _compact_ports("10001; 10002") == "10001; 10002"

row = _normalize_visible_row({
    "NAT / Порты": "tcp 10562→10562; tcp 10563→10563",
    "Последняя активность": "jan/01/1970 00:00:00",
    "Дней без связи": "20681",
})
assert row["NAT / Порты"] == "10562; 10563"
assert row["Последняя активность"] == "Никогда"
assert row["Дней без связи"] == ""

change = _describe_port_change(
    "tcp 10001→10001; tcp 10002→10002",
    "10002; 10003",
)
assert "добавлены 10003" in change
assert "удалены 10001" in change

app_source = (ROOT / "linkvideo_vpn_helper" / "app.py").read_text(encoding="utf-8")
resilience_pos = app_source.index("install_vpn_sheets_resilience()")
operator_pos = app_source.index("install_vpn_sheets_operator_view()")
assert resilience_pos < operator_pos

module_source = (ROOT / "linkvideo_vpn_helper" / "services" / "vpn_sheets_operator_view.py").read_text(encoding="utf-8")
assert "self._lv_summary_rows = {}" in module_source
assert "(2, 3), (3, 4)" in module_source
assert "Автоматически" in module_source

print("CORE TESTS 3.0.13 SHEETS OPERATOR VIEW OK")
