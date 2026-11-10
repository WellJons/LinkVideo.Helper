from __future__ import annotations

"""Offline fleet-wide LV1/LV2/UI/Sheets regression; no production connections."""

import time
from datetime import datetime
from unittest.mock import patch

from linkvideo_vpn_helper.services.vpn_lifecycle import (
    DAY_NS,
    has_lv_metadata,
    parse_lv_comment,
)
from linkvideo_vpn_helper.services.vpn_retention_policy import (
    compose_extended_comment,
)
from linkvideo_vpn_helper.services.vpn_service import (
    SessionCredentials,
    VPNService,
)
from linkvideo_vpn_helper.services.vpn_sheets_retention_compat import (
    _deleted_reason,
    install_vpn_sheets_retention_compat,
)
from linkvideo_vpn_helper.services.vpn_sheets_resilience import (
    install_vpn_sheets_resilience,
)
from linkvideo_vpn_helper.services.vpn_sheets_operator_view import (
    install_vpn_sheets_operator_view,
)

TODAY = time.time_ns() // DAY_NS
CREDS = SessionCredentials("offline-only", "not-a-password")


def past(days: int) -> int:
    return (TODAY - days) * DAY_NS


def row(name: str, comment: str, *, disabled: str = "no") -> dict:
    return {
        ".id": "*1",
        "name": name,
        "profile": name,
        "remote-address": "172.16.1.2",
        "disabled": disabled,
        "last-logged-out": "jan/01/1970 00:00:00",
        "comment": comment,
        "password": "TEST-ONLY",
    }


class FakeRouterOS:
    def __init__(self, secrets: list[dict]):
        self.secrets = secrets

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def print(self, path, *_args, **_kwargs):
        return self.secrets if path == "/ppp/secret" else []


class OfflineVPNService(VPNService):
    def __init__(self, secrets: list[dict]):
        super().__init__()
        self.secrets = secrets

    def fetch_config_snapshot(self, _server, _creds):
        return {
            "secrets": self.secrets,
            "profiles": [],
            "actives": [],
            "nat_rules": [],
        }


class OfflineSheets:
    def __init__(self, existing=None):
        self.rows = list(existing or [])
        self.deleted = []
        self.history = []

    def ensure_auxiliary_sheets(self):
        pass

    def read_server_rows(self, _server):
        return list(self.rows)

    def archive_deleted_rows(self, _server, rows):
        self.deleted.extend(rows)

    def append_history(self, rows):
        self.history.extend(rows)

    def write_server_rows(self, _server, rows, _previous_count):
        self.rows = list(rows)

    def remove_deleted_rows(self, _server, _logins):
        pass

    def update_summary(self, _server, _synced_at, _result):
        pass


def main() -> None:
    old = past(400)
    sleeping = compose_extended_comment("tech note", "S", past(40), old, "inactive_30")
    quarantined = compose_extended_comment("tech note", "Q", past(100), old, "inactive_90")
    never_confirmed = compose_extended_comment("tech note", "U", 0, past(85), "created")
    unknown = compose_extended_comment("tech note", "U", 0, past(85), "never_active_tracking")
    legacy = f"tech note |LV1|state=Q|last={past(101)}|ver=1.0.0|"
    clients = [
        row("sleeping", sleeping),
        row("quarantined", quarantined, disabled="yes"),
        row("never", never_confirmed),
        row("unknown", unknown),
        row("legacy", legacy, disabled="yes"),
    ]

    assert has_lv_metadata(sleeping)
    assert has_lv_metadata(legacy)
    assert not has_lv_metadata("just a comment")
    assert parse_lv_comment(sleeping).state == "S"
    assert parse_lv_comment(quarantined).state == "Q"

    # VPN-client page must honour LV2 even though it also understands LV1.
    with patch(
        "linkvideo_vpn_helper.services.vpn_service.RouterOSAPIClient",
        side_effect=lambda *_a, **_k: FakeRouterOS(clients),
    ):
        for server in ("vpn01.linkvideo.ru", "vpn02.linkvideo.ru", "kz-vpn01.linkvideo.ru"):
            records = VPNService().list_lifecycle_clients(server, CREDS)
            by_login = {item.login: item for item in records}
            assert len(by_login) == len(clients), server
            assert by_login["sleeping"].lifecycle_state == "S"
            assert by_login["quarantined"].lifecycle_state == "Q"
            assert by_login["never"].lifecycle_state == "U"
            assert by_login["unknown"].lifecycle_state == "U"
            assert by_login["sleeping"].lifecycle_source == "lv"
            assert by_login["unknown"].lifecycle_source == "lv"
            assert by_login["legacy"].lifecycle_source == "lv"
            assert by_login["sleeping"].last_logged_out_dt is not None
            assert by_login["unknown"].last_logged_out_dt is None

    # Exactly the same sources and statuses are used for the fleet-wide mirror.
    install_vpn_sheets_retention_compat()
    install_vpn_sheets_resilience()
    install_vpn_sheets_operator_view()
    from linkvideo_vpn_helper.services import vpn_sheets_sync as sheets

    current = sheets.build_current_clients(OfflineVPNService(clients), {
        "secrets": clients, "profiles": [], "actives": [], "nat_rules": []
    })
    mirror = {item.login: item for item in current}
    assert mirror["sleeping"].lifecycle_code == "S"
    assert mirror["quarantined"].lifecycle_code == "Q"
    assert mirror["sleeping"].row["Lifecycle"] == "Спящая 30+"
    assert mirror["sleeping"].row["Последняя активность"] not in {"Никогда", "Неизвестна"}
    assert mirror["unknown"].row["Последняя активность"] == "Неизвестна"
    assert mirror["never"].row["Последняя активность"] == "Никогда"
    assert mirror["unknown"].row["Дней без связи"] == ""
    assert "неизвестна" in mirror["unknown"].row["Причина"].lower()

    backend = OfflineSheets()
    result = sheets.VPNSheetsSyncService(OfflineVPNService(clients), backend).sync_server(
        "vpn03.linkvideo.ru", CREDS,
    )
    assert result.clients == len(clients) and result.deleted == 0
    assert len(backend.rows) == len(clients)

    # Disappearance is not proof that LV-Aging deleted a client.
    old_row = dict(mirror["unknown"].row)
    old_row["Первое обнаружение"] = "2026-08-16 18:02:43"
    old_row["Удалена"] = "Нет"
    offline = OfflineSheets([old_row])
    gone = sheets.VPNSheetsSyncService(OfflineVPNService([]), offline).sync_server(
        "vpn02.linkvideo.ru", CREDS, source="Ручная сверка RouterOS",
        initiator="operator-who-only-exported",
    )
    assert gone.deleted == 1
    assert offline.deleted[0]["Lifecycle"] == "Удалена"
    assert "неизвестны" in offline.deleted[0]["Причина"].lower()
    assert offline.deleted[0]["Кто удалил"] == "Неизвестно (обнаружено при сверке)"
    assert offline.history[0][8] == "Неизвестно (обнаружено при сверке)"

    # The 85-day age exists only with verified Helper creation metadata.
    known_reason = _deleted_reason(
        {"Комментарий RouterOS": never_confirmed},
        "Ручная сверка RouterOS",
        datetime.now(),
    )
    assert "85 дн." in known_reason
    assert "не подтверждена" in known_reason
    unknown_reason = _deleted_reason(
        {"Комментарий RouterOS": unknown, "Первое обнаружение": "2026-08-16 18:02:43"},
        "Ручная сверка RouterOS",
        datetime.now(),
    )
    assert "неизвестны" in unknown_reason.lower()

    # The Google export must never call apply_policy_now or other destructive
    # RouterOS operations; cleanup is a separate, explicitly enabled LV task.
    assert "apply_policy_now" not in (
        sheets.VPNSheetsSyncService.sync_server.__code__.co_names
    )

    print("CORE TESTS LV2 FLEET SHEETS CONSISTENCY OK")


if __name__ == "__main__":
    main()
