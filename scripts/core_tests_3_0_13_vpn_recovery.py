from __future__ import annotations

import json
from pathlib import Path

from linkvideo_vpn_helper.services.vpn_restore_service import VPNRestoreService
from linkvideo_vpn_helper.services.vpn_sheets_sync import CurrentClient, reconcile_records


ROOT = Path(__file__).resolve().parents[1]


class _FakeBackend:
    def read_deleted_rows(self, server: str):
        rows = [
            {
                "VPN-сервер": "vpn01.linkvideo.ru",
                "Логин": "client01",
                "Пароль": "SavedPass42",
                "Remote Address": "172.16.1.10",
                "Profile": "client01",
                "Удалена": "Да",
                "Удалена в": "2026-09-01 03:20:00",
                "NAT / Порты": "tcp 10001→10001; tcp 10002→10002 [off]",
                "RouterOS snapshot": json.dumps(
                    {
                        "secret": {"name": "client01", "password": "SavedPass42"},
                        "profile": {"name": "client01"},
                        "nat_rules": [],
                    }
                ),
            }
        ]
        if not server:
            return rows
        return [row for row in rows if row.get("VPN-сервер") == server]

    def search_deleted_rows(self, query: str):
        wanted = str(query or "").lower()
        return [
            row for row in self.read_deleted_rows("")
            if wanted in str(row.get("Логин", "")).lower()
        ]

    def remove_deleted_rows(self, server: str, logins):
        return None

    def find_deleted_row(self, server: str, login: str):
        return next(
            (
                row
                for row in self.read_deleted_rows(server)
                if row.get("Логин") == login
            ),
            None,
        )


class _FakeVPN:
    @staticmethod
    def _parse_ports(value):
        return [int(value)] if str(value or "").isdigit() else []

    @staticmethod
    def _find_free_ports(used_ports, count):
        result = []
        for port in range(10001, 13001):
            if port in used_ports:
                continue
            result.append(port)
            if len(result) >= count:
                break
        return result


def main() -> int:
    previous = {
        "Логин": "client01",
        "Пароль": "DoNotLoseMe",
        "Service": "l2tp",
        "Profile": "client01",
        "Local Address": "172.31.255.254",
        "Remote Address": "172.16.1.10",
        "Комментарий RouterOS": "client01",
        "NAT / Порты": "tcp 10001→10001",
        "Lifecycle": "Активная",
        "PPP disabled": "Нет",
        "Удалена": "Нет",
        "RouterOS snapshot": json.dumps(
            {"secret": {"name": "client01", "password": "DoNotLoseMe"}}
        ),
    }
    current = CurrentClient(
        login="client01",
        lifecycle_code="A",
        row={
            "Логин": "client01",
            "Пароль": "",
            "Service": "l2tp",
            "Profile": "client01",
            "Local Address": "172.31.255.254",
            "Remote Address": "172.16.1.10",
            "Комментарий RouterOS": "client01",
            "NAT / Порты": "tcp 10001→10001",
            "Lifecycle": "Активная",
            "PPP disabled": "Нет",
            "RouterOS snapshot": json.dumps(
                {"secret": {"name": "client01"}}
            ),
        },
    )
    reconciled = reconcile_records(
        "vpn01.linkvideo.ru",
        [previous],
        [current],
        source="test",
        initiator="test",
    )
    assert reconciled.rows[0]["Пароль"] == "DoNotLoseMe"
    snapshot = json.loads(reconciled.rows[0]["RouterOS snapshot"])
    assert snapshot["secret"]["password"] == "DoNotLoseMe"
    assert "Пароль" not in (reconciled.history[0][4] if reconciled.history else "")

    restore = VPNRestoreService(_FakeVPN(), _FakeBackend())
    deleted = restore.list_deleted(["vpn01.linkvideo.ru"])
    assert len(deleted) == 1
    searched = restore.search_deleted("client", ["vpn01.linkvideo.ru"])
    assert len(searched) == 1
    assert deleted[0].password_saved is True
    assert deleted[0].login == "client01"

    fallback = restore._fallback_nat(
        deleted[0].row,
        deleted[0].login,
        deleted[0].remote_address,
    )
    assert len(fallback) == 2
    assert fallback[0]["dst-port"] == "10001"
    assert fallback[1]["disabled"] == "yes"

    planned, replacements = restore._remap_occupied_ports(
        fallback,
        {10001, 10003},
    )
    planned_by_original = {original: payload for original, payload in planned}
    # Occupied 10001 must not be reclaimed, and replacement allocation must not
    # steal still-free old 10002.
    assert replacements == {10001: 10004}
    assert planned_by_original[10001]["dst-port"] == "10004"
    assert planned_by_original[10001]["to-ports"] == "10001"
    assert planned_by_original[10002]["dst-port"] == "10002"
    assert planned_by_original[10002]["to-ports"] == "10002"

    service_source = (
        ROOT / "linkvideo_vpn_helper" / "services" / "vpn_restore_service.py"
    ).read_text(encoding="utf-8")
    ui_source = (
        ROOT / "linkvideo_vpn_helper" / "ui" / "vpn_sheets_sync_integration.py"
    ).read_text(encoding="utf-8")
    assert "Автоматическое восстановление заблокировано" in service_source
    assert "_generate_password" not in service_source
    assert "_remap_occupied_ports" in service_source
    assert "port_replacements" in service_source
    assert "Восстановить клиента" in ui_source

    print("CORE TESTS VPN RECOVERY AND PASSWORD PRESERVATION OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
