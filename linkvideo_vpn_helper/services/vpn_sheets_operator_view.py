from __future__ import annotations

"""Operator-facing normalization for the RouterOS -> Google Sheets mirror.

Keep recovery data lossless in RouterOS snapshot while making the visible LV tabs
compact and the audit log readable. This adapter runs after the retention and
resilience layers so it can normalize legacy 3.0.12 rows without changing restore
semantics or the robust Google transport.
"""

import json
import re
import threading
from datetime import datetime

from linkvideo_vpn_helper.services.vpn_retention_policy import parse_extended_comment
from linkvideo_vpn_helper.services.vpn_service import VPNService


_INSTALLED = False


def _compact_ports(value: str) -> str:
    result: list[str] = []
    for part in str(value or "").split(";"):
        token = part.strip()
        if not token:
            continue
        match = re.match(r"(?:(?:tcp|udp)\s+)?([^→\s]+)\s*→", token, re.I)
        port = match.group(1) if match else token
        if "[off]" in token.lower() and "[off]" not in port.lower():
            port += " [off]"
        if port not in result:
            result.append(port)
    return "; ".join(result)


def _port_set(value: str) -> set[str]:
    return {
        part.strip().replace(" [off]", "")
        for part in _compact_ports(value).split(";")
        if part.strip()
    }


def _normalize_visible_row(row: dict[str, str]) -> dict[str, str]:
    item = dict(row)
    item["NAT / Порты"] = _compact_ports(str(item.get("NAT / Порты", "") or ""))

    # RouterOS commonly exposes an epoch value for a secret that has never logged
    # out. It is technically a timestamp, but operationally it means "never".
    last = str(item.get("Последняя активность", "") or "").strip().lower()
    if last.startswith("jan/01/1970") or last.startswith("1970-01-01"):
        item["Последняя активность"] = "Никогда"
        item["Дней без связи"] = ""
    return item



def _known_activity_from_recovery(row: dict[str, str]) -> datetime | None:
    """Recover only dates that were actually stored by RouterOS/LV.

    A saved RouterOS snapshot can contain last-logged-out even when an older
    operator view incorrectly replaced its visible value with 'Никогда'.
    Do not reconstruct dates from "Первое обнаружение" or creation metadata.
    """
    candidates: list[datetime] = []
    visible = VPNService.parse_router_datetime(str(row.get("Последняя активность", "") or ""))
    if visible:
        candidates.append(visible)

    comments = [str(row.get("Комментарий RouterOS", "") or "")]
    raw_snapshot = str(row.get("RouterOS snapshot", "") or "")
    try:
        snapshot = json.loads(raw_snapshot) if raw_snapshot else {}
    except (ValueError, TypeError):
        snapshot = {}
    if isinstance(snapshot, dict):
        secret = snapshot.get("secret")
        if isinstance(secret, dict):
            comments.append(str(secret.get("comment", "") or ""))
            router_last = VPNService.parse_router_datetime(
                str(secret.get("last-logged-out", "") or secret.get("last_logged_out", "") or "")
            )
            if router_last:
                candidates.append(router_last)

    for comment in comments:
        last_ns = int(parse_extended_comment(comment).last_ns or 0)
        if last_ns > 0:
            try:
                candidates.append(datetime.fromtimestamp(last_ns / 1_000_000_000))
            except (OverflowError, OSError, ValueError):
                continue
    return max(candidates) if candidates else None


def _restore_activity_from_recovery(
    row: dict[str, str], *, archived: bool = False
) -> dict[str, str]:
    """Correct an old incorrectly hidden activity date without guessing one."""
    updated = dict(row)
    observed = _known_activity_from_recovery(updated)
    if observed is not None:
        old_visible = VPNService.parse_router_datetime(
            str(updated.get("Последняя активность", "") or "")
        )
        if old_visible is None or old_visible < observed:
            updated["Последняя активность"] = observed.replace(microsecond=0).isoformat(sep=" ")
            if archived:
                reference = VPNService.parse_router_datetime(str(updated.get("Удалена в", "") or ""))
                if reference is None:
                    reference = datetime.now()
            else:
                reference = datetime.now()
            days = max(0, (reference - observed).days)
            updated["Дней без связи"] = str(days)
            if archived:
                reason = str(updated.get("Причина", "") or "").lower()
                if any(token in reason for token in (
                    "без единой активности", "история подключений", "причина удаления неизвест",
                )):
                    updated["Причина"] = (
                        f"Удалена в RouterOS; последняя активность {days} дн. назад "
                        "(причина удаления не подтверждена)"
                    )
        return updated

    # No reliable timestamp: distinguish never-connected clients (LV2 created)
    # from migrated old accounts with an unknown past. The 1970 sentinel means
    # unavailable history, not proof of no connection.
    if str(updated.get("Последняя активность", "") or "").strip().lower() in {
        "", "никогда", "неизвестна", "jan/01/1970 00:00:00", "1970-01-01 00:00:00",
    }:
        meta = parse_extended_comment(str(updated.get("Комментарий RouterOS", "") or ""))
        verified_never = meta.reason in {"created", "never_active_30"}
        updated["Последняя активность"] = "Никогда" if verified_never else "Неизвестна"
        updated["Дней без связи"] = ""
    return updated

def _describe_port_change(before: str, after: str) -> str:
    old = _port_set(before)
    new = _port_set(after)
    added = sorted(new - old, key=lambda x: (len(x), x))
    removed = sorted(old - new, key=lambda x: (len(x), x))
    bits: list[str] = []
    if added:
        bits.append("добавлены " + ", ".join(added))
    if removed:
        bits.append("удалены " + ", ".join(removed))
    return "Порты: " + "; ".join(bits) if bits else "Порты без изменения"


def install_vpn_sheets_operator_view() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    import linkvideo_vpn_helper.services.vpn_sheets_sync as sheets

    original_build = sheets.build_current_clients
    original_reconcile = sheets.reconcile_records
    backend_cls = sheets.GoogleSheetsBackend
    original_prepare_sync = getattr(backend_cls, "prepare_sync", None)
    original_apply_operator_view = getattr(backend_cls, "apply_operator_view", None)

    def build_current_clients(service, snapshot):
        current = original_build(service, snapshot)
        active_names = {
            str(row.get("name", "") or "").strip()
            for row in list(snapshot.get("actives") or [])
            if str(row.get("name", "") or "").strip()
        }
        for record in current:
            record.row.update(_normalize_visible_row(record.row))
            meta = parse_extended_comment(str(record.row.get("Комментарий RouterOS", "") or ""))
            if record.login not in active_names:
                # Keep the actual RouterOS last-logged-out when LV2 l=0.
                # Older code incorrectly replaced known dates with "Никогда".
                record.row.update(_restore_activity_from_recovery(record.row))
        return current

    def reconcile_records(server, existing_rows, current_clients, *, source, initiator, now=None, sync_id=None, capture_history=True):
        # Normalize legacy visible values before comparison. Otherwise the first
        # 3.0.13 sync would create thousands of fake "NAT changed" events merely
        # because 3.0.12 stored `tcp 10001→10001` instead of `10001`.
        normalized_existing = [
            _restore_activity_from_recovery(_normalize_visible_row(row))
            for row in list(existing_rows or [])
        ]
        before = {
            str(row.get("Логин", "") or "").strip(): row
            for row in normalized_existing
            if str(row.get("Логин", "") or "").strip()
        }

        # RouterOS can temporarily omit last-logged-out. Do not erase a
        # previously observed date for the *same* account, but never carry it
        # into a recreated/replaced account with a different password or IP.
        for record in current_clients:
            old = before.get(str(record.login or "").strip())
            if not old:
                continue
            prior_password = str(old.get("Пароль", "") or "").strip()
            current_password = str(record.row.get("Пароль", "") or "").strip()
            prior_ip = str(old.get("Remote Address", "") or "").strip()
            current_ip = str(record.row.get("Remote Address", "") or "").strip()
            same_account = bool(
                prior_password and current_password and prior_ip and current_ip
                and prior_password == current_password and prior_ip == current_ip
            )
            if not same_account:
                continue
            old_last = _known_activity_from_recovery(old)
            now_last = _known_activity_from_recovery(record.row)
            if old_last and (now_last is None or old_last > now_last):
                record.row["Последняя активность"] = old_last.replace(
                    microsecond=0
                ).isoformat(sep=" ")
                record.row["Дней без связи"] = str(max(
                    0, (datetime.now() - old_last).days
                ))

        result = original_reconcile(
            server,
            normalized_existing,
            current_clients,
            source=source,
            initiator=initiator,
            now=now,
            sync_id=sync_id,
            capture_history=capture_history,
        )
        after = {
            str(row.get("Логин", "") or "").strip(): row
            for row in result.rows
            if str(row.get("Логин", "") or "").strip()
        }

        for row in result.rows:
            row.update(_normalize_visible_row(row))
        for row in result.archived:
            row.update(_restore_activity_from_recovery(_normalize_visible_row(row), archived=True))

        for history in result.history:
            if len(history) < 10:
                continue
            login = str(history[2] or "").strip()
            action = str(history[3] or "").strip()
            fields = [part.strip() for part in str(history[4] or "").split(",") if part.strip()]
            old = before.get(login, {})
            new = after.get(login, {})

            # Do not expose an implementation label as the operator name.
            if str(history[8] or "").strip().lower() in {
                "linkvideo.helper auto-sync",
                "linkvideo.helper",
                "auto-sync",
            }:
                history[8] = "Автоматически"

            if action == "Обнаружена на RouterOS":
                history[3] = "Новая VPN-учётка"
                ip = str(new.get("Remote Address", "") or "").strip()
                ports = _compact_ports(str(new.get("NAT / Порты", "") or ""))
                details = []
                if ip:
                    details.append(f"IP {ip}")
                if ports:
                    details.append(f"порты {ports}")
                history[4] = " · ".join(details) or "Обнаружена в RouterOS"
            elif action == "Восстановлена на RouterOS":
                history[3] = "Восстановлена VPN-учётка"
                history[4] = "Восстановлена из сохранённых данных"
            elif action == "Изменена":
                field_set = set(fields)
                if field_set == {"NAT / Порты"}:
                    history[3] = "Изменены NAT-порты"
                    history[4] = _describe_port_change(
                        str(old.get("NAT / Порты", "") or ""),
                        str(new.get("NAT / Порты", "") or ""),
                    )
                elif field_set == {"Remote Address"}:
                    history[3] = "Изменён VPN IP"
                    history[4] = (
                        f"{old.get('Remote Address', '—') or '—'} → "
                        f"{new.get('Remote Address', '—') or '—'}"
                    )
                elif field_set == {"Profile"}:
                    history[3] = "Изменён профиль"
                    history[4] = "Изменён RouterOS Profile"
                elif field_set == {"Пароль"}:
                    history[3] = "Изменён пароль"
                    history[4] = "Пароль VPN-учётки изменён"
                elif field_set == {"PPP disabled"}:
                    history[3] = "Изменено состояние PPP"
                    history[4] = (
                        f"{old.get('PPP disabled', '—') or '—'} → "
                        f"{new.get('PPP disabled', '—') or '—'}"
                    )
                elif "Lifecycle" not in field_set:
                    history[3] = "Изменена конфигурация"
                    history[4] = ", ".join(fields) or "Параметры VPN-учётки"

        return result

    def apply_operator_view(self, server_sheets) -> None:
        if callable(original_apply_operator_view):
            original_apply_operator_view(self, server_sheets)
        if getattr(self, "_lv_compact_columns_ready", False):
            return

        base_url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}"
        metadata = self._request(
            "GET",
            base_url,
            params={"fields": "sheets(properties(sheetId,title))"},
        )
        ids = {
            str(dict(item.get("properties") or {}).get("title", "") or ""):
            int(dict(item.get("properties") or {}).get("sheetId"))
            for item in list(metadata.get("sheets") or [])
            if dict(item.get("properties") or {}).get("sheetId") is not None
        }
        requests_payload = []
        for title in list(server_sheets or []):
            sheet_id = ids.get(str(title))
            if sheet_id is None:
                continue
            # Service and Profile are recovery fields, but normally duplicate
            # information an operator already sees. Keep them stored, hide them.
            for start_index, end_index in ((2, 3), (3, 4)):
                requests_payload.append({
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": start_index,
                            "endIndex": end_index,
                        },
                        "properties": {"hiddenByUser": True},
                        "fields": "hiddenByUser",
                    }
                })
        deleted_id = ids.get(str(sheets.DELETED_SHEET))
        if deleted_id is not None:
            # Deleted sheet: Service=D (index 3), Profile=E (index 4).
            for start_index, end_index in ((3, 4), (4, 5)):
                requests_payload.append({
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": deleted_id,
                            "dimension": "COLUMNS",
                            "startIndex": start_index,
                            "endIndex": end_index,
                        },
                        "properties": {"hiddenByUser": True},
                        "fields": "hiddenByUser",
                    }
                })
        if requests_payload:
            self._request("POST", f"{base_url}:batchUpdate", payload={"requests": requests_payload})
        self._lv_compact_columns_ready = True

    def prepare_sync(self, servers) -> None:
        # LV Сводка was intentionally removed. The resilience layer caches summary
        # rows; pre-seed that cache with an empty mapping so its preflight never
        # tries to read a non-existent sheet.
        self._lv_summary_rows = {}
        server_sheets = [sheets.sheet_for_server(host) for host in list(servers or [])]
        if callable(original_prepare_sync):
            original_prepare_sync(self, servers)
        else:
            self.ensure_auxiliary_sheets()
        # The resilience preflight calls its lexically captured view function, so
        # explicitly apply this final extension afterwards as well.
        apply_operator_view(self, server_sheets)

    def update_summary(self, server, synced_at, result) -> None:
        # Kept as a strict no-op: LV Сводка no longer exists by design.
        return None

    original_sync_server = sheets.VPNSheetsSyncService.sync_server
    archive_repair_gate = threading.RLock()

    def repair_deleted_archive(self) -> int:
        """Backfill archived last-seen dates only from saved RouterOS evidence.

        Run during an explicit manual backup, not continuously. Lock against
        other server workers writing the same shared deleted sheet.
        """
        lock = getattr(self, "_lv_deleted_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._lv_deleted_lock = lock
        with lock:
            rows = self.read_deleted_rows("")
            repaired = 0
            output = []
            for old in rows:
                new = _restore_activity_from_recovery(old, archived=True)
                repaired += int(any(
                    str(old.get(key, "") or "") != str(new.get(key, "") or "")
                    for key in ("Последняя активность", "Дней без связи", "Причина")
                ))
                output.append(new)
            if repaired:
                self.put_values(
                    f"'{sheets.DELETED_SHEET}'!A2:Q{len(output) + 1}",
                    [self._deleted_dict_to_row(row) for row in output],
                )
            return repaired

    def sync_server(self, server, creds, *, source="RouterOS sync", initiator=""):
        result = original_sync_server(self, server, creds, source=source, initiator=initiator)
        if source == "Ручная сверка RouterOS":
            repair = getattr(self.backend, "repair_deleted_archive", None)
            if callable(repair):
                # Full backup uses several workers, so only one repairs the
                # shared archive; the backend lock protects concurrent writes.
                with archive_repair_gate:
                    if not getattr(self.backend, "_lv_archived_activity_repaired", False):
                        try:
                            changed = repair()
                        except Exception as exc:
                            from linkvideo_vpn_helper.services.app_logging import event
                            event("SHEETS", "Не удалось восстановить даты из архивных снимков", str(exc)[:220], level=30)
                        else:
                            self.backend._lv_archived_activity_repaired = True
                            if changed:
                                from linkvideo_vpn_helper.services.app_logging import event
                                event("SHEETS", "Восстановлена подтверждённая активность в архиве", f"строк: {changed}")
        return result

    sheets.VPNSheetsSyncService.sync_server = sync_server
    backend_cls.repair_deleted_archive = repair_deleted_archive
    sheets.build_current_clients = build_current_clients
    sheets.reconcile_records = reconcile_records
    backend_cls.apply_operator_view = apply_operator_view
    backend_cls.prepare_sync = prepare_sync
    backend_cls.update_summary = update_summary
    _INSTALLED = True
