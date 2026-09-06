from __future__ import annotations

"""Operator-facing normalization for the RouterOS -> Google Sheets mirror.

Keep recovery data lossless in RouterOS snapshot while making the visible LV tabs
compact and the audit log readable. This adapter runs after the retention and
resilience layers so it can normalize legacy 3.0.12 rows without changing restore
semantics or the robust Google transport.
"""

import re

from linkvideo_vpn_helper.services.vpn_retention_policy import parse_extended_comment


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
            if record.login not in active_names and meta.last_ns <= 0:
                record.row["Последняя активность"] = "Никогда"
                record.row["Дней без связи"] = ""
        return current

    def reconcile_records(server, existing_rows, current_clients, *, source, initiator, now=None, sync_id=None):
        # Normalize legacy visible values before comparison. Otherwise the first
        # 3.0.13 sync would create thousands of fake "NAT changed" events merely
        # because 3.0.12 stored `tcp 10001→10001` instead of `10001`.
        normalized_existing = [_normalize_visible_row(row) for row in list(existing_rows or [])]
        before = {
            str(row.get("Логин", "") or "").strip(): row
            for row in normalized_existing
            if str(row.get("Логин", "") or "").strip()
        }

        result = original_reconcile(
            server,
            normalized_existing,
            current_clients,
            source=source,
            initiator=initiator,
            now=now,
            sync_id=sync_id,
        )
        after = {
            str(row.get("Логин", "") or "").strip(): row
            for row in result.rows
            if str(row.get("Логин", "") or "").strip()
        }

        for row in result.rows:
            row.update(_normalize_visible_row(row))
        for row in result.archived:
            row.update(_normalize_visible_row(row))

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
        if callable(original_prepare_sync):
            original_prepare_sync(self, servers)
        else:
            self.ensure_auxiliary_sheets()
            apply_operator_view(self, [sheets.sheet_for_server(host) for host in list(servers or [])])

    def update_summary(self, server, synced_at, result) -> None:
        # Kept as a strict no-op: LV Сводка no longer exists by design.
        return None

    sheets.build_current_clients = build_current_clients
    sheets.reconcile_records = reconcile_records
    backend_cls.apply_operator_view = apply_operator_view
    backend_cls.prepare_sync = prepare_sync
    backend_cls.update_summary = update_summary
    _INSTALLED = True
