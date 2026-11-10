from __future__ import annotations

"""Offline regression: no LV История I/O and no deleted lookup in live search."""

from pathlib import Path
from linkvideo_vpn_helper.services.vpn_service import VPNService, SessionCredentials
from linkvideo_vpn_helper.services.vpn_sheets_retention_compat import install_vpn_sheets_retention_compat
from linkvideo_vpn_helper.services.vpn_sheets_resilience import install_vpn_sheets_resilience
from linkvideo_vpn_helper.services.vpn_sheets_operator_view import install_vpn_sheets_operator_view
from linkvideo_vpn_helper.services import vpn_sheets_sync as sheets

ROOT = Path(__file__).resolve().parents[1]


class FakeVPN(VPNService):
    def __init__(self):
        super().__init__()
        self.secret_rows = [{
            ".id": "*A", "name": "client-demo", "password": "test-not-real",
            "profile": "client-demo", "service": "l2tp", "disabled": "no",
            "local-address": "172.31.255.254", "remote-address": "172.16.1.22",
            "last-logged-out": "nov/01/2026 12:30:00", "comment": "demo",
        }]

    def fetch_config_snapshot(self, _server, _creds):
        return {
            "secrets": self.secret_rows,
            "actives": [], "profiles": [], "nat_rules": [],
        }


class FakeBackend:
    def __init__(self):
        self.rows = []
        self.archived = []
        self.written = []
        self.history_calls = 0

    def ensure_auxiliary_sheets(self):
        pass

    def read_server_rows(self, _server):
        return list(self.rows)

    def archive_deleted_rows(self, _server, rows):
        self.archived.extend(rows)

    def append_history(self, _rows):
        self.history_calls += 1
        raise AssertionError("LV История must never receive writes")

    def write_server_rows(self, _server, rows, _previous_count):
        self.rows = list(rows)
        self.written.append(list(rows))

    def remove_deleted_rows(self, _server, _logins):
        pass

    def update_summary(self, _server, _synced_at, _result):
        pass


class SchemaProbe:
    spreadsheet_id = "offline-only"
    def __init__(self):
        self.requests = []
        self.writes = []

    def _request(self, method, _url, *, params=None, payload=None):
        self.requests.append((method, params, payload))
        if method == "GET":
            return {"sheets": []}
        return {}

    def put_values(self, range_, values):
        self.writes.append((range_, values))


def main():
    assert sheets.HISTORY_LOGGING_ENABLED is False
    install_vpn_sheets_retention_compat()
    install_vpn_sheets_resilience()
    install_vpn_sheets_operator_view()

    # Record reconciliation still reports factual changes and deleted backups.
    # Normal sync does not build history rows at all.
    account = FakeVPN()
    credentials = SessionCredentials("offline", "offline")
    for server in ("vpn01.linkvideo.ru", "vpn10.linkvideo.ru", "kz-vpn01.linkvideo.ru"):
        backend = FakeBackend()
        service = sheets.VPNSheetsSyncService(account, backend)
        inserted = service.sync_server(server, credentials, source="Ручная сверка RouterOS")
        assert inserted.clients == 1 and inserted.added == 1
        assert backend.history_calls == 0
        assert len(backend.rows) == 1

        # Direct callers can still request an in-memory audit for diagnostics.
        direct = sheets.reconcile_records(
            server, [], sheets.build_current_clients(account, account.fetch_config_snapshot(server, credentials)),
            source="test", initiator="test",
        )
        assert direct.history
        quiet = sheets.reconcile_records(
            server, [], sheets.build_current_clients(account, account.fetch_config_snapshot(server, credentials)),
            source="test", initiator="test", capture_history=False,
        )
        assert quiet.history == []

        account.secret_rows = []
        removed = service.sync_server(server, credentials, source="Автосверка RouterOS")
        assert removed.deleted == 1
        assert len(backend.archived) == 1
        assert backend.archived[0]["Логин"] == "client-demo"
        assert backend.history_calls == 0
        account.__init__()

    # On new workbooks, create LV Удалённые but do not create or write LV История.
    probe = SchemaProbe()
    sheets.GoogleSheetsBackend.ensure_auxiliary_sheets(probe)
    for method, _params, payload in probe.requests:
        if method == "POST":
            names = [
                item.get("addSheet", {}).get("properties", {}).get("title")
                for item in payload.get("requests", [])
            ]
            assert sheets.DELETED_SHEET in names
            assert sheets.HISTORY_SHEET not in names
    assert all(sheets.HISTORY_SHEET not in target for target, _ in probe.writes)

    # VPN lookup no longer starts a parallel Google Sheets deleted-account query.
    ui = (ROOT / "linkvideo_vpn_helper/ui/pages/search_manage_page.py").read_text(encoding="utf-8")
    handler = ui.split("    def _on_search(self, report):", 1)[1].split("    def _on_deleted_search(", 1)[0]
    assert "search_deleted_async" not in handler
    assert "archive_lookup" not in handler
    assert "Проверяю удалённые" not in handler
    assert "Клиент не найден" in handler

    print("CORE TESTS LV2 NO HISTORY / NO DELETED LOOKUP OK")


if __name__ == "__main__":
    main()
