from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from linkvideo_vpn_helper.services.vpn_sheets_sync import (
    CurrentClient,
    SERVER_COLUMNS,
    reconcile_records,
    sheet_for_server,
)


def current(login: str, password: str = "pass1", ports: str = "tcp 10001→10001", lifecycle: str = "Активная") -> CurrentClient:
    row = {name: "" for name in SERVER_COLUMNS}
    row.update({
        "Логин": login,
        "Пароль": password,
        "Service": "l2tp",
        "Profile": login,
        "Local Address": "172.31.255.254",
        "Remote Address": "172.16.1.176",
        "Комментарий RouterOS": "ручная заметка |LV1|state=A|last=100|ver=1.0.1|",
        "NAT / Порты": ports,
        "Lifecycle": lifecycle,
        "PPP disabled": "Нет",
        "Последняя активность": "2026-08-16 15:00:00",
        "Дней без связи": "0",
        "RouterOS snapshot": json.dumps({"secret": {"name": login, "password": password}}, ensure_ascii=False),
    })
    return CurrentClient(login, row, "A")


fixed_now = datetime(2026, 8, 16, 16, 30, 0)

# Листы строго разделены по VPN-серверам.
assert sheet_for_server("vpn01.linkvideo.ru") == "LV vpn01"
assert sheet_for_server("rb-vpn01.linkvideo.ru") == "LV rb-vpn01"
assert sheet_for_server("kz-vpn01.linkvideo.ru") == "LV kz-vpn01"

# Первая полная сверка создаёт строку и историю обнаружения.
first = reconcile_records(
    "vpn01.linkvideo.ru", [], [current("89000000001")],
    source="Автосверка RouterOS", initiator="test", now=fixed_now, sync_id="sync-first",
)
assert first.added == 1 and first.changed == 0 and first.deleted == 0
assert len(first.rows) == 1
assert first.rows[0]["Удалена"] == "Нет"
assert first.rows[0]["Первое обнаружение"] == "2026-08-16 16:30:00"
assert first.history[0][3] == "Обнаружена на RouterOS"

# Наблюдаемая активность и служебный LV last= меняются постоянно, но не должны
# засорять историю или менять дату реального изменения конфигурации.
quiet_client = current("89000000001")
quiet_client.row["Последняя активность"] = "2026-08-16 16:31:00"
quiet_client.row["Дней без связи"] = "1"
quiet_client.row["Комментарий RouterOS"] = "ручная заметка |LV1|state=A|last=999999|ver=1.0.1|"
quiet = reconcile_records(
    "vpn01.linkvideo.ru", [dict(first.rows[0])], [quiet_client],
    source="Автосверка RouterOS", initiator="agent", now=datetime(2026, 8, 16, 16, 31, 0), sync_id="sync-quiet",
)
assert quiet.changed == 0
assert quiet.history == []
assert quiet.rows[0]["Последняя активность"] == "2026-08-16 16:31:00"
assert quiet.rows[0]["Дней без связи"] == "1"
assert quiet.rows[0]["Последнее изменение"] == first.rows[0]["Последнее изменение"]

# Реальное изменение портов/пароля обновляет запись, но пароль не копируется
# открытым текстом в журнал Было/Стало.
existing = [dict(quiet.rows[0])]
changed_client = current("89000000001", password="new-secret", ports="tcp 10001→10001; tcp 10002→10002")
changed = reconcile_records(
    "vpn01.linkvideo.ru", existing, [changed_client],
    source="Helper · добавление портов", initiator="operator", now=fixed_now, sync_id="sync-change",
)
assert changed.changed == 1
assert changed.rows[0]["Пароль"] == "new-secret"
assert "Пароль" in changed.history[0][4]
assert "new-secret" not in changed.history[0][5]
assert "new-secret" not in changed.history[0][6]
assert "<изменено>" in changed.history[0][6]

# Пропажа учётки после УСПЕШНОГО RouterOS snapshot не удаляет строку из базы,
# а только ставит признак удаления и сохраняет последние реквизиты.
removed = reconcile_records(
    "vpn01.linkvideo.ru", [dict(changed.rows[0])], [],
    source="Автосверка RouterOS", initiator="agent", now=fixed_now, sync_id="sync-delete",
)
assert removed.deleted == 1
assert len(removed.rows) == 1
assert removed.rows[0]["Удалена"] == "Да"
assert removed.rows[0]["Пароль"] == "new-secret"
assert removed.rows[0]["Удалена в"] == "2026-08-16 16:30:00"
assert removed.history[0][3] == "Удалена на RouterOS"

# Повторная сверка уже удалённой строки не плодит повторные события.
removed_again = reconcile_records(
    "vpn01.linkvideo.ru", [dict(removed.rows[0])], [],
    source="Автосверка RouterOS", initiator="agent", now=fixed_now, sync_id="sync-delete2",
)
assert removed_again.deleted == 0
assert removed_again.history == []

# Если учётка снова появилась на RouterOS — строка оживает, а не создаётся дубль.
restored = reconcile_records(
    "vpn01.linkvideo.ru", [dict(removed.rows[0])], [changed_client],
    source="Автосверка RouterOS", initiator="agent", now=fixed_now, sync_id="sync-restore",
)
assert restored.restored == 1 and restored.added == 0
assert len(restored.rows) == 1
assert restored.rows[0]["Удалена"] == "Нет"
assert restored.rows[0]["Удалена в"] == ""
assert restored.history[0][3] == "Восстановлена на RouterOS"

# Важный контракт безопасности находится в sync_server: reconciliation вызывается
# только ПОСЛЕ успешного fetch_config_snapshot. Проверяем порядок по исходнику.
source_text = (root / "linkvideo_vpn_helper/services/vpn_sheets_sync.py").read_text(encoding="utf-8")
fetch_pos = source_text.index("snapshot = self.vpn_service.fetch_config_snapshot")
read_pos = source_text.index("existing_rows = self.backend.read_server_rows")
reconcile_pos = source_text.index("result = reconcile_records")
assert fetch_pos < read_pos < reconcile_pos
assert "ни одна старая строка не может быть ошибочно помечена как удалённая" in source_text
assert "result.history" in source_text and source_text.index("self.backend.append_history") < source_text.index("self.backend.write_server_rows")

print("CORE TESTS 3.0.8 VPN SHEETS SYNC OK")
