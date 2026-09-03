from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.services.vpn_retention_policy import DAY_NS, compose_extended_comment
from linkvideo_vpn_helper.services.vpn_sheets_retention_compat import install_vpn_sheets_retention_compat

install_vpn_sheets_retention_compat()
from linkvideo_vpn_helper.services import vpn_sheets_sync as sheets


NOW = datetime(2026, 8, 16, 12, 0, 0)
NOW_NS = int(NOW.timestamp() * 1_000_000_000)


def ago(days: int) -> int:
    return NOW_NS - days * DAY_NS


q_comment = compose_extended_comment("", "Q", ago(100), ago(500), "inactive_90")
q_row = {
    "Логин": "q-user",
    "Пароль": "x",
    "Service": "l2tp",
    "Profile": "q-user",
    "Local Address": "172.31.255.254",
    "Remote Address": "172.16.1.10",
    "Комментарий RouterOS": q_comment,
    "NAT / Порты": "tcp 10000→10000",
    "Lifecycle": "Карантин 90+",
    "PPP disabled": "Да",
    "Последняя активность": "2026-05-08 12:00:00",
    "Дней без связи": "100",
    "Причина": "Отключена автоматически: нет активности 90+ дней",
}
current = [sheets.CurrentClient("q-user", dict(q_row), "Q")]
previous = [{
    **q_row,
    "Комментарий RouterOS": compose_extended_comment("", "A", ago(100), ago(500), "tracked"),
    "Lifecycle": "Активная",
    "PPP disabled": "Нет",
    "Причина": "Активность была менее 30 дней назад",
    "Первое обнаружение": "2025-01-01 00:00:00",
    "Последнее изменение": "2026-05-01 00:00:00",
    "Удалена": "Нет",
}]

result = sheets.reconcile_records(
    "vpn-test",
    previous,
    current,
    source="Helper · включение автокарантина",
    initiator="test",
    now=NOW,
    sync_id="test-q",
)
assert result.changed == 1
assert result.rows[0]["Причина"] == "Отключена автоматически: нет активности 90+ дней"
assert result.rows[0]["PPP disabled"] == "Да"
assert any("Отключена автоматически: 90+ дней без активности" == row[3] for row in result.history), result.history

# When a 365+ account disappears from RouterOS, the old LV marker must explain
# the deletion even if the spreadsheet's wall-clock timestamps are incomplete.
delete_comment = compose_extended_comment("", "M", ago(370), ago(500), "manual_disabled")
deleted_existing = [{
    "Логин": "old-user",
    "Пароль": "x",
    "Service": "l2tp",
    "Profile": "old-user",
    "Комментарий RouterOS": delete_comment,
    "NAT / Порты": "tcp 10100→10100",
    "Lifecycle": "Отключена вручную",
    "PPP disabled": "Да",
    "Последняя активность": "",
    "Дней без связи": "",
    "Первое обнаружение": "2026-08-01 00:00:00",
    "Последнее изменение": "2026-08-01 00:00:00",
    "Удалена": "Нет",
    "Причина": "Отключена вручную через Helper",
}]
deleted = sheets.reconcile_records(
    "vpn-test",
    deleted_existing,
    [],
    source="Автосверка RouterOS",
    initiator="RouterOS / неизвестно",
    now=NOW,
    sync_id="test-del",
)
assert deleted.rows == []
assert len(deleted.archived) == 1
row = deleted.archived[0]
assert row["Удалена"] == "Да"
assert row["Причина"].startswith("Удалена автоматически: 370"), row["Причина"]
assert any(history[3].startswith("Удалена автоматически: 370") for history in deleted.history), deleted.history

assert sheets.SERVER_COLUMNS[-1] == "Причина"
assert len(sheets.SERVER_COLUMNS) == 20

print("CORE TESTS 3.0.10 SHEETS LIFECYCLE OK")
