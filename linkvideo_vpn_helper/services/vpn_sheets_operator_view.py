from __future__ import annotations

"""Operator-facing normalization for the RouterOS -> Google Sheets mirror.

Keep recovery data lossless in RouterOS snapshot while making the visible LV tabs
compact and the audit log readable. This adapter deliberately runs after the
retention/sheets compatibility layer, so it can normalize both legacy 3.0.12 rows
and newly fetched RouterOS rows without changing restore semantics.
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

            # New history generated by the base layer is already compact text;
            # ensure a legacy verbose NAT representation never leaks back into it.
            history[5] = re.sub(
                r"(?i)(Порты:\s*)[^;]+(?:;\s*[^;]+)*",
                lambda m: m.group(0),
                str(history[5] or ""),
            )
            history[6] = str(history[6] or "")

        return result

    sheets.build_current_clients = build_current_clients
    sheets.reconcile_records = reconcile_records
    _INSTALLED = True
