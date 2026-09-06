from __future__ import annotations

from datetime import datetime
from pathlib import Path

from linkvideo_vpn_helper.services import vpn_retention_policy as policy
from linkvideo_vpn_helper.services.vpn_sheets_sync import (
    CurrentClient,
    DELETED_SHEET,
    HISTORY_COLUMNS,
    reconcile_records,
)


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    assert DELETED_SHEET == "LV Удалённые"
    assert HISTORY_COLUMNS[3] == "Действие"
    assert HISTORY_COLUMNS[4] == "Что изменилось"
    assert HISTORY_COLUMNS[8] == "Кто изменил"
    assert HISTORY_COLUMNS[9] == "ID операции"

    previous = {
        "Логин": "client01",
        "Пароль": "Secret42",
        "Service": "l2tp",
        "Profile": "client01",
        "Local Address": "172.31.255.254",
        "Remote Address": "172.16.1.10",
        "Комментарий RouterOS": "client01",
        "NAT / Порты": "tcp 10001→10001",
        "Lifecycle": "Активная",
        "PPP disabled": "Нет",
        "Последняя активность": "2026-08-01 10:00:00",
        "Дней без связи": "33",
        "Первое обнаружение": "2026-07-01 10:00:00",
        "Последнее изменение": "2026-08-01 10:00:00",
        "Удалена": "Нет",
        "Удалена в": "",
        "Источник": "test",
        "RouterOS snapshot": "{}",
    }
    removed = reconcile_records(
        "vpn01.linkvideo.ru",
        [previous],
        [],
        source="Автосверка RouterOS",
        initiator="RouterOS / неизвестно",
        now=datetime(2026, 9, 3, 12, 0, 0),
        sync_id="archive-test",
    )
    assert removed.rows == []
    assert len(removed.archived) == 1
    assert removed.archived[0]["VPN-сервер"] == "vpn01.linkvideo.ru"
    assert removed.archived[0]["Пароль"] == "Secret42"
    assert removed.history[0][8] == "RouterOS / неизвестно"
    # New history rows are compact operator text, not JSON dumps.
    assert removed.history[0][5] != ""
    assert not removed.history[0][5].lstrip().startswith("{")
    assert not removed.history[0][6].lstrip().startswith("{")

    assert policy.NEVER_ACTIVE_DELETE_DAYS == 30
    assert policy.DELETE_DAYS == 365
    aging = policy.aging_script_source()
    assert "never_active_30" in aging
    assert "deleteAfter" in aging
    assert "NEVER_ACTIVE_DELETE_DAYS" not in aging  # rendered to the numeric threshold
    state, reason = policy._desired_state(
        0,
        1_000 * policy.DAY_NS,
        False,
        False,
        (1_000 + 31) * policy.DAY_NS,
    )
    assert state == "R" and reason == "never_active_30"

    search_ui = (ROOT / "linkvideo_vpn_helper/ui/pages/search_manage_page.py").read_text(encoding="utf-8")
    assert "_add_deleted_result" in search_ui
    assert "_restore_deleted" in search_ui
    assert "Удалён · можно восстановить" in search_ui
    assert "Нет ни активной, ни удалённой" in search_ui

    integration = (ROOT / "linkvideo_vpn_helper/ui/vpn_sheets_sync_integration.py").read_text(encoding="utf-8")
    assert "deletedSearchReady" in integration
    assert "restore_deleted_async" in integration
    assert "RouterOS / неизвестно" in integration

    compat = (ROOT / "linkvideo_vpn_helper/services/vpn_sheets_retention_compat.py").read_text(encoding="utf-8")
    assert "LV Automation / RouterOS" in compat
    assert "без единой активности" in compat

    resilience = (ROOT / "linkvideo_vpn_helper/services/vpn_sheets_resilience.py").read_text(encoding="utf-8")
    assert "apply_operator_view" in resilience
    assert '"hiddenByUser": True' in resilience

    print("CORE TESTS 3.0.13 VPN HISTORY SEARCH ARCHIVE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
