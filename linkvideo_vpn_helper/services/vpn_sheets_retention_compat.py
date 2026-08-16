from __future__ import annotations

"""Expose lifecycle/retention reasons in the RouterOS -> Google Sheets mirror."""

from datetime import datetime

from linkvideo_vpn_helper.services.app_logging import event
from linkvideo_vpn_helper.services.vpn_retention_policy import parse_extended_comment


_INSTALLED = False


def _parse_dt(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for parser in (
        lambda: datetime.fromisoformat(text),
        lambda: datetime.strptime(text, "%Y-%m-%d %H:%M:%S"),
    ):
        try:
            return parser()
        except Exception:
            pass
    return None


def _reason_text(reason: str, state: str = "") -> str:
    mapping = {
        "created": "Создана; ожидается первая активность",
        "never_active_tracking": "Ни одной активности; идёт годовой отсчёт",
        "tracked": "Активность была менее 30 дней назад",
        "activity": "Активность подтверждена RouterOS",
        "inactive_30": "Нет активности 30+ дней",
        "inactive_90": "Отключена автоматически: нет активности 90+ дней",
        "inactive_365": "Подлежит автоматическому удалению: 365+ дней без активности",
        "never_active_365": "Подлежит автоматическому удалению: 365+ дней без единой активности",
        "manual_disabled": "Отключена вручную через Helper",
        "manual_enabled": "Включена вручную через Helper",
        "manual_or_external_disabled": "Отключена вручную или напрямую в RouterOS",
        "auto_restore": "Восстановлена автоматически после попытки входа",
    }
    value = mapping.get(str(reason or "").strip(), "")
    if value:
        return value
    fallback = {
        "Q": "Отключена автоматически: нет активности 90+ дней",
        "S": "Нет активности 30+ дней",
        "M": "Отключена вручную или напрямую в RouterOS",
        "U": "Ни одной подтверждённой активности",
        "R": "Подлежит автоматическому удалению: 365+ дней без активности",
        "A": "Активна",
    }
    return fallback.get(str(state or "").strip().upper(), "")


def _deleted_reason(old: dict[str, str], source: str, now: datetime) -> str:
    low_source = str(source or "").lower()
    if "helper" in low_source and "удален" in low_source:
        return "Удалена вручную через Helper"

    last_dt = _parse_dt(old.get("Последняя активность", ""))
    first_dt = _parse_dt(old.get("Первое обнаружение", ""))
    if last_dt is not None:
        days = max(0, (now - last_dt).days)
        if days >= 365:
            return f"Удалена автоматически: {days} дн. без активности"
    if last_dt is None and first_dt is not None:
        days = max(0, (now - first_dt).days)
        if days >= 365:
            return f"Удалена автоматически: {days} дн. без единой активности"
    old_reason = str(old.get("Причина", "") or "").strip()
    if "365+" in old_reason:
        return old_reason.replace("Подлежит автоматическому удалению", "Удалена автоматически")
    return "Удалена в RouterOS; причина не подтверждена"


def _history_event(reason: str, row: dict[str, str], source: str) -> str | None:
    low_source = str(source or "").lower()
    disabled = str(row.get("PPP disabled", "") or "").strip().lower() in {"да", "yes", "true", "1"}
    if reason == "inactive_90" and disabled:
        return "Отключена автоматически: 90+ дней без активности"
    if reason == "inactive_30":
        return "Переведена в спящие: 30+ дней без активности"
    if reason == "auto_restore":
        return "Восстановлена автоматически после попытки входа"
    if reason == "manual_disabled":
        return "Отключена вручную через Helper"
    if reason == "manual_enabled":
        return "Включена вручную через Helper"
    if reason == "manual_or_external_disabled" and disabled:
        return "Отключена вручную/в RouterOS" if "helper" not in low_source else "Отключена вручную через Helper"
    return None


def install_vpn_sheets_retention_compat() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    import linkvideo_vpn_helper.services.vpn_sheets_sync as sheets

    if "Причина" not in sheets.SERVER_COLUMNS:
        sheets.SERVER_COLUMNS = (*sheets.SERVER_COLUMNS, "Причина")
    if "Причина" not in sheets.COMPARE_COLUMNS:
        sheets.COMPARE_COLUMNS = (*sheets.COMPARE_COLUMNS, "Причина")

    original_build = sheets.build_current_clients
    original_reconcile = sheets.reconcile_records

    def build_current_clients(service, snapshot):
        current = original_build(service, snapshot)
        for record in current:
            comment = str(record.row.get("Комментарий RouterOS", "") or "")
            meta = parse_extended_comment(comment)
            record.row["Причина"] = _reason_text(meta.reason, meta.state)
        return current

    def reconcile_records(server, existing_rows, current_clients, *, source, initiator, now=None, sync_id=None):
        moment = now or datetime.now()
        before = {
            str(row.get("Логин", "") or "").strip(): dict(row)
            for row in list(existing_rows or [])
            if str(row.get("Логин", "") or "").strip()
        }
        current_by_login = {
            str(item.login or "").strip(): item
            for item in current_clients
            if str(item.login or "").strip()
        }

        result = original_reconcile(
            server,
            existing_rows,
            current_clients,
            source=source,
            initiator=initiator,
            now=moment,
            sync_id=sync_id,
        )

        newly_deleted: dict[str, str] = {}
        output_by_login: dict[str, dict[str, str]] = {}
        for row in result.rows:
            login = str(row.get("Логин", "") or "").strip()
            if login:
                output_by_login[login] = row
            old = before.get(login, {})
            deleted_now = str(row.get("Удалена", "") or "").strip().lower() in {"да", "yes", "true", "1"}
            was_deleted = str(old.get("Удалена", "") or "").strip().lower() in {"да", "yes", "true", "1"}
            if deleted_now and not was_deleted and login not in current_by_login:
                reason = _deleted_reason(old, source, moment)
                row["Причина"] = reason
                newly_deleted[login] = reason
                event("SHEETS", "Причина удаления VPN", f"{server} · {login} · {reason}")
            elif deleted_now and not row.get("Причина"):
                row["Причина"] = str(old.get("Причина", "") or "Удалена в RouterOS")

        # Replace generic "Изменена" audit rows with operator-readable lifecycle
        # events. This keeps the server sheet and LV История equally useful.
        for history_row in result.history:
            if len(history_row) < 4:
                continue
            login = str(history_row[2] or "").strip()
            if login in newly_deleted:
                reason_text = newly_deleted[login]
                history_row[3] = reason_text
                continue

            item = current_by_login.get(login)
            row = output_by_login.get(login, {})
            if item is None:
                continue
            meta = parse_extended_comment(str(item.row.get("Комментарий RouterOS", "") or ""))
            specific = _history_event(meta.reason, row, source)
            if specific:
                history_row[3] = specific

        return result

    sheets.build_current_clients = build_current_clients
    sheets.reconcile_records = reconcile_records

    def read_server_rows(self, server: str):
        sheet = sheets.sheet_for_server(server)
        values = self.get_values(f"'{sheet}'!A2:T1500")
        result = []
        for row in values:
            item = sheets._row_to_dict(row)
            if item.get("Логин", "").strip():
                result.append(item)
        return result

    def write_server_rows(self, server: str, rows, previous_count: int):
        if len(rows) > sheets.MAX_SERVER_ROWS:
            raise RuntimeError(f"{server}: в лист не помещается {len(rows)} строк")
        sheet = sheets.sheet_for_server(server)
        self.put_values(f"'{sheet}'!T1", [["Причина"]])
        encoded_rows = [sheets._dict_to_row(row) for row in rows]
        write_count = max(len(encoded_rows), int(previous_count))
        if write_count <= 0:
            return
        while len(encoded_rows) < write_count:
            encoded_rows.append([""] * len(sheets.SERVER_COLUMNS))
        self.put_values(f"'{sheet}'!A2:T{write_count + 1}", encoded_rows)

    sheets.GoogleSheetsBackend.read_server_rows = read_server_rows
    sheets.GoogleSheetsBackend.write_server_rows = write_server_rows
    _INSTALLED = True
