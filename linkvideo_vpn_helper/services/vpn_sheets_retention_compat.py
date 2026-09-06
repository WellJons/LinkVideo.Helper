from __future__ import annotations

"""Expose lifecycle/retention reasons in the RouterOS -> Google Sheets mirror."""

from datetime import datetime
import re

from linkvideo_vpn_helper.services.app_logging import event
from linkvideo_vpn_helper.services.vpn_retention_policy import DAY_NS, parse_extended_comment


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


def _external_ports_text(value: str) -> str:
    """Keep the operator-facing NAT column compact: external ports only.

    Full protocol/to-port data remains available in RouterOS snapshot and is used
    by restore logic. The visible sheet should not repeat ``tcp 10001→10001`` for
    the common 1:1 mapping.
    """
    result: list[str] = []
    for part in str(value or "").split(";"):
        token = part.strip()
        if not token:
            continue
        match = re.match(r"(?:(?:tcp|udp)\s+)?([^→\s]+)\s*→", token, re.I)
        if match:
            suffix = " [off]" if "[off]" in token.lower() else ""
            result.append(f"{match.group(1)}{suffix}")
        else:
            result.append(token)
    return "; ".join(result)


def _reason_text(reason: str, state: str = "") -> str:
    mapping = {
        "created": "Создана; ожидается первая активность",
        "never_active_tracking": "Ни одной активности; удаление через 30 дней от создания",
        "tracked": "Активность была менее 30 дней назад",
        "activity": "Активность подтверждена RouterOS",
        "inactive_30": "Нет активности 30+ дней",
        "inactive_90": "Отключена автоматически: нет активности 90+ дней",
        "inactive_365": "Подлежит автоматическому удалению: 365+ дней без активности",
        "never_active_30": "Подлежит автоматическому удалению: 30+ дней без единой активности",
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
        "U": "Ни одной подтверждённой активности; удаление через 30 дней",
        "R": "Подлежит автоматическому удалению: 365+ дней без активности",
        "A": "Активна",
    }
    return fallback.get(str(state or "").strip().upper(), "")


def _deleted_reason(old: dict[str, str], source: str, now: datetime) -> str:
    low_source = str(source or "").lower()
    if "helper" in low_source and "удален" in low_source:
        return "Удалена вручную через Helper"

    # Prefer the LV marker because it is the exact reference used by LV-Aging.
    meta = parse_extended_comment(str(old.get("Комментарий RouterOS", "") or ""))
    now_ns = int(now.timestamp() * 1_000_000_000)
    if meta.last_ns > 0:
        days = max(0, int((now_ns - meta.last_ns) // DAY_NS))
        if days >= 365:
            return f"Удалена автоматически: {days} дн. без активности"
    if meta.last_ns <= 0 and meta.created_ns > 0:
        days = max(0, int((now_ns - meta.created_ns) // DAY_NS))
        if days >= 30:
            return f"Удалена автоматически: {days} дн. без единой активности"

    last_dt = _parse_dt(old.get("Последняя активность", ""))
    first_dt = _parse_dt(old.get("Первое обнаружение", ""))
    if last_dt is not None:
        days = max(0, (now - last_dt).days)
        if days >= 365:
            return f"Удалена автоматически: {days} дн. без активности"
    if last_dt is None and first_dt is not None:
        days = max(0, (now - first_dt).days)
        if days >= 30:
            return f"Удалена автоматически: {days} дн. без единой активности"
    old_reason = str(old.get("Причина", "") or "").strip()
    if "365+" in old_reason or "30+" in old_reason:
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


def _retention_overdue(rows: list[dict[str, str]], now: datetime | None = None) -> list[str]:
    """Return clients that should already have been removed by LV-Aging."""
    moment = now or datetime.now()
    overdue: list[str] = []
    for row in rows:
        login = str(row.get("Логин", "") or "").strip()
        if not login:
            continue
        try:
            days = int(str(row.get("Дней без связи", "") or "").strip())
        except Exception:
            days = -1
        if days >= 365:
            overdue.append(login)
            continue

        # Never-connected accounts have no numeric inactivity age. Use the first
        # successful mirror observation as a conservative fallback: only after 30
        # full days in Sheets is the safety sweep allowed to enforce deletion.
        lifecycle = str(row.get("Lifecycle", "") or "").strip()
        if lifecycle == "Последняя активность неизвестна":
            first = _parse_dt(str(row.get("Первое обнаружение", "") or ""))
            if first is not None and (moment - first).days >= 30:
                overdue.append(login)
    return overdue


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
    original_sync_server = sheets.VPNSheetsSyncService.sync_server

    def build_current_clients(service, snapshot):
        current = original_build(service, snapshot)
        for record in current:
            comment = str(record.row.get("Комментарий RouterOS", "") or "")
            meta = parse_extended_comment(comment)
            record.row["Причина"] = _reason_text(meta.reason, meta.state)
            record.row["NAT / Порты"] = _external_ports_text(
                str(record.row.get("NAT / Порты", "") or "")
            )
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
        output_by_login: dict[str, dict[str, str]] = {
            str(row.get("Логин", "") or "").strip(): row
            for row in result.rows
            if str(row.get("Логин", "") or "").strip()
        }
        for row in result.archived:
            login = str(row.get("Логин", "") or "").strip()
            old = before.get(login, {})
            was_deleted = str(old.get("Удалена", "") or "").strip().lower() in {"да", "yes", "true", "1"}
            if not was_deleted and login not in current_by_login:
                reason = _deleted_reason(old, source, moment)
                row["Причина"] = reason
                row["Кто удалил"] = str(initiator or "RouterOS")
                newly_deleted[login] = reason
                event("SHEETS", "Причина удаления VPN", f"{server} · {login} · {reason}")
            elif not row.get("Причина"):
                row["Причина"] = str(old.get("Причина", "") or "Удалена в RouterOS")
            if not row.get("Кто удалил"):
                row["Кто удалил"] = str(old.get("Кто удалил", "") or initiator or "RouterOS")

        # Replace generic "Изменена" audit rows with operator-readable lifecycle
        # events. This keeps the server sheet and LV История equally useful.
        for history_row in result.history:
            if len(history_row) < 4:
                continue
            login = str(history_row[2] or "").strip()
            if login in newly_deleted:
                history_row[3] = newly_deleted[login]
                if str(newly_deleted[login]).startswith("Удалена автоматически"):
                    history_row[8] = "LV Automation / RouterOS"
                continue

            item = current_by_login.get(login)
            row = output_by_login.get(login, {})
            if item is None:
                continue
            meta = parse_extended_comment(str(item.row.get("Комментарий RouterOS", "") or ""))
            specific = _history_event(meta.reason, row, source)
            if specific:
                history_row[3] = specific
                if meta.reason in {"inactive_30", "inactive_90", "auto_restore"}:
                    history_row[8] = "LV Automation / RouterOS"

        return result

    sheets.build_current_clients = build_current_clients
    sheets.reconcile_records = reconcile_records

    def ensure_sheet_columns(self, sheet: str, minimum: int = 20) -> None:
        """Grow legacy A:S server tabs before any A:T read/write.

        Existing LinkVideo sheets were created with exactly 19 columns. 3.0.10
        adds the twentieth ``Причина`` column, and the Sheets Values API refuses
        an A:T range until the underlying grid itself has been expanded.
        """
        minimum = max(1, int(minimum))
        cache = getattr(self, "_lv_min_columns_ready", None)
        if cache is None:
            cache = set()
            setattr(self, "_lv_min_columns_ready", cache)
        cache_key = (str(sheet), minimum)
        if cache_key in cache:
            return

        base_url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}"
        metadata = self._request(
            "GET",
            base_url,
            params={"fields": "sheets(properties(sheetId,title,gridProperties(columnCount)))"},
        )
        properties = None
        for item in list(metadata.get("sheets") or []):
            candidate = dict(item.get("properties") or {})
            if str(candidate.get("title", "")) == str(sheet):
                properties = candidate
                break
        if properties is None:
            # Keep the normal Values API error for an actually missing tab.
            return

        grid = dict(properties.get("gridProperties") or {})
        current = int(grid.get("columnCount", 0) or 0)
        if current < minimum:
            sheet_id = properties.get("sheetId")
            if sheet_id is None:
                raise RuntimeError(f"Google Sheets: у листа {sheet} отсутствует sheetId")
            self._request(
                "POST",
                f"{base_url}:batchUpdate",
                payload={
                    "requests": [{
                        "appendDimension": {
                            "sheetId": int(sheet_id),
                            "dimension": "COLUMNS",
                            "length": minimum - current,
                        }
                    }]
                },
            )
            event("SHEETS", "Расширен лист VPN", f"{sheet}: {current} → {minimum} столбцов")
        cache.add(cache_key)

    def read_server_rows(self, server: str):
        sheet = sheets.sheet_for_server(server)
        self.ensure_sheet_columns(sheet, len(sheets.SERVER_COLUMNS))
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
        self.ensure_sheet_columns(sheet, len(sheets.SERVER_COLUMNS))
        self.put_values(f"'{sheet}'!T1", [["Причина"]])
        encoded_rows = [sheets._dict_to_row(row) for row in rows]
        write_count = max(len(encoded_rows), int(previous_count))
        if write_count <= 0:
            return
        while len(encoded_rows) < write_count:
            encoded_rows.append([""] * len(sheets.SERVER_COLUMNS))
        self.put_values(f"'{sheet}'!A2:T{write_count + 1}", encoded_rows)

    def update_summary(self, server, synced_at, result) -> None:
        # LV Сводка removed from the live workbook. Keep the base sync call as a
        # harmless no-op instead of issuing a failing Values API request each run.
        return None

    def sync_server(self, server, creds, *, source="RouterOS sync", initiator=""):
        # First mirror the complete RouterOS state to Google. This guarantees a
        # recovery snapshot exists before any retention safety sweep can delete
        # RouterOS objects.
        result = original_sync_server(
            self,
            server,
            creds,
            source=source,
            initiator=initiator,
        )
        try:
            rows = self.backend.read_server_rows(server)
            overdue = _retention_overdue(rows)
            if not overdue:
                return result

            # Respect an operator-disabled LV-Aging switch. The safety sweep only
            # compensates for a missed/failed daily scheduler when retention itself
            # is actually enabled on this server.
            from linkvideo_vpn_helper.services.vpn_automation_service import VPNAutomationService
            from linkvideo_vpn_helper.services.vpn_retention_policy import apply_policy_now

            status = VPNAutomationService().get_status(server, creds)
            if not status.aging_enabled:
                event(
                    "LV",
                    "Просрочено retention-удаление",
                    f"{server} · {len(overdue)} учёток 365+/never-active 30+, но LV-Aging выключен",
                    level=30,
                )
                return result

            counts = apply_policy_now(server, creds)
            if int(counts.get("deleted", 0) or 0) <= 0:
                event(
                    "LV",
                    "Retention safety sweep не удалил просроченные учётки",
                    f"{server} · найдено {len(overdue)}: {', '.join(overdue[:8])}",
                    level=30,
                )
                return result

            # Re-read after successful deletion so disappeared accounts are moved
            # to LV Удалённые immediately, with the last full snapshot from the
            # first pass preserved as the recovery source.
            return original_sync_server(
                self,
                server,
                creds,
                source=source,
                initiator=initiator,
            )
        except Exception as exc:
            event(
                "LV",
                "Ошибка retention safety sweep",
                f"{server} · {str(exc)[:220]}",
                level=30,
            )
            return result

    sheets.GoogleSheetsBackend.ensure_sheet_columns = ensure_sheet_columns
    sheets.GoogleSheetsBackend.read_server_rows = read_server_rows
    sheets.GoogleSheetsBackend.write_server_rows = write_server_rows
    sheets.GoogleSheetsBackend.update_summary = update_summary
    sheets.VPNSheetsSyncService.sync_server = sync_server
    _INSTALLED = True
