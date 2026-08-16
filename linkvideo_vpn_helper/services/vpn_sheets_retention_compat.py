from __future__ import annotations

"""Expose lifecycle/retention reasons in the RouterOS → Google Sheets mirror."""

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


def _reason_text(reason: str) -> str:
    mapping = {
        "created": "Создана; ожидается первая активность",
        "never_active_tracking": "Ни одной активности; идёт годовой отсчёт",
        "tracked": "Активность была менее 30 дней назад",
        "activity": "Активность подтверждена RouterOS",
        "inactive_30": "Нет активности 30+ дней",
        "inactive_90": "Отключена автоматически: нет активности 90+ дней",
        "manual_disabled": "Отключена вручную через Helper",
        "manual_enabled": "Включена вручную через Helper",
        "manual_or_external_disabled": "Отключена вручную или напрямую в RouterOS",
        "auto_restore": "Восстановлена автоматически после попытки входа",
    }
    return mapping.get(str(reason or "").strip(), str(reason or "").strip())


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
    return "Удалена в RouterOS; причина не подтверждена"


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
            record.row["Причина"] = _reason_text(meta.reason)
        return current

    def reconcile_records(server, existing_rows, current_clients, *, source, initiator, now=None, sync_id=None):
        moment = now or datetime.now()
        before = {
            str(row.get("Логин", "") or "").strip(): dict(row)
            for row in list(existing_rows or [])
            if str(row.get("Логин", "") or "").strip()
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
        current_logins = {str(item.login or "").strip() for item in current_clients}
        newly_deleted: dict[str, str] = {}
        for row in result.rows:
            login = str(row.get("Логин", "") or "").strip()
            old = before.get(login, {})
            deleted_now = str(row.get("Удалена", "") or "").strip().lower() in {"да", "yes", "true", "1"}
            was_deleted = str(old.get("Удалена", "") or "").strip().lower() in {"да", "yes", "true", "1"}
            if deleted_now and not was_deleted and login not in current_logins:
                reason = _deleted_reason(old, source, moment)
                row["Причина"] = reason
                newly_deleted[login] = reason
                event("SHEETS", "Причина удаления VPN", f"{server} · {login} · {reason}")
            elif deleted_now and not row.get("Причина"):
                row["Причина"] = str(old.get("Причина", "") or "Удалена в RouterOS")

        if newly_deleted:
            for history_row in result.history:
                if len(history_row) < 4:
                    continue
                login = str(history_row[2] or "").strip()
                if login not in newly_deleted:
                    continue
                reason = newly_deleted[login]
                if reason.startswith("Удалена автоматически"):
                    history_row[3] = reason
                elif reason.startswith("Удалена вручную"):
                    history_row[3] = "Удалена вручную через Helper"
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
        # Existing A:S columns stay untouched; only the new T column is appended.
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
