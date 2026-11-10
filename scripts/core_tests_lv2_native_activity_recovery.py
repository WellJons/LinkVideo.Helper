from __future__ import annotations

"""Offline regression for LV2 native last-logged-out and archived recovery."""

import json
from datetime import datetime
from unittest.mock import patch

from linkvideo_vpn_helper.services.vpn_retention_policy import compose_extended_comment
from linkvideo_vpn_helper.services.vpn_service import VPNService
from linkvideo_vpn_helper.services.vpn_sheets_retention_compat import install_vpn_sheets_retention_compat
from linkvideo_vpn_helper.services.vpn_sheets_resilience import install_vpn_sheets_resilience
from linkvideo_vpn_helper.services.vpn_sheets_operator_view import (
    _known_activity_from_recovery,
    _restore_activity_from_recovery,
    install_vpn_sheets_operator_view,
)
from linkvideo_vpn_helper.services import vpn_sheets_sync as sheets


def main():
    install_vpn_sheets_retention_compat()
    install_vpn_sheets_resilience()
    install_vpn_sheets_operator_view()

    date = datetime(2024, 6, 15, 5, 42, 32)
    never = "jan/01/1970 00:00:00"
    marker = compose_extended_comment(
        "old account", "U", 0,
        int(datetime(2026, 8, 16).timestamp() * 1_000_000_000),
        "never_active_tracking",
    )
    secret = {
        ".id": "*51", "name": "existing-old-account",
        "comment": marker,
        "profile": "existing-old-account",
        "remote-address": "172.16.1.75",
        "last-logged-out": "jun/15/2024 05:42:32",
        "disabled": "no",
        "password": "OFFLINE-DUMMY",
    }
    no_history = dict(secret, name="legacy-unknown", **{
        "last-logged-out": never,
    })
    new_account = dict(secret, name="verified-never", **{
        "last-logged-out": never,
        "comment": compose_extended_comment(
            "new account", "U", 0,
            int(datetime(2026, 8, 16).timestamp() * 1_000_000_000),
            "created",
        ),
    })
    snapshot = {
        "secrets": [secret, no_history, new_account],
        "profiles": [], "actives": [], "nat_rules": [],
    }
    records = sheets.build_current_clients(VPNService(), snapshot)
    by_login = {item.login: item for item in records}
    assert by_login["existing-old-account"].row["Последняя активность"] == "2024-06-15 05:42:32"
    assert int(by_login["existing-old-account"].row["Дней без связи"]) > 365
    assert by_login["legacy-unknown"].row["Последняя активность"] == "Неизвестна"
    assert by_login["verified-never"].row["Последняя активность"] == "Никогда"

    # Previous versions could write Never despite a real native date being in
    # the hidden recovery snapshot. Recover the date without RouterOS access.
    prior_row = dict(by_login["existing-old-account"].row)
    prior_row["Последняя активность"] = "Никогда"
    prior_row["Дней без связи"] = ""
    repaired = _restore_activity_from_recovery(prior_row)
    assert repaired["Последняя активность"] == "2024-06-15 05:42:32"
    assert int(repaired["Дней без связи"]) > 365
    assert _known_activity_from_recovery(prior_row) == date

    archive = dict(prior_row)
    archive["Удалена в"] = "2026-11-10 14:07:32"
    archive["Причина"] = (
        "Удалена в RouterOS; история подключений и причина удаления неизвестны"
    )
    updated = _restore_activity_from_recovery(archive, archived=True)
    assert updated["Последняя активность"] == "2024-06-15 05:42:32"
    assert "последняя активность" in updated["Причина"].lower()
    assert "причина удаления не подтверждена" in updated["Причина"]

    # Absence of activity in both metadata and snapshot never invents a date.
    unknown_row = dict(by_login["legacy-unknown"].row)
    unknown_row["Последняя активность"] = "Никогда"
    unknown_row["RouterOS snapshot"] = json.dumps({"secret": no_history})
    unknown_fixed = _restore_activity_from_recovery(unknown_row, archived=True)
    assert unknown_fixed["Последняя активность"] == "Неизвестна"

    # An already archived row can be repaired in place without invoking
    # RouterOS; every stored field not part of activity recovery stays intact.
    class FakeBackend:
        def __init__(self):
            self.rows = [dict(archive)]
            self.writes = []

        def read_deleted_rows(self, server):
            assert server == ""
            return self.rows

        def _deleted_dict_to_row(self, item):
            return sheets.GoogleSheetsBackend._deleted_dict_to_row(item)

        def put_values(self, range_, values):
            self.writes.append((range_, values))

    backend = FakeBackend()
    count = sheets.GoogleSheetsBackend.repair_deleted_archive(backend)
    assert count == 1
    assert len(backend.writes) == 1
    assert "A2:Q2" in backend.writes[0][0]
    assert backend.writes[0][1][0][sheets.DELETED_COLUMNS.index("Пароль")] == "OFFLINE-DUMMY"

    print("CORE TESTS LV2 NATIVE ACTIVITY RECOVERY OK")


if __name__ == "__main__":
    main()
