from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials

from .config import get_settings
from .db import VPNDatabase


DEFAULT_SPREADSHEET_ID = "1KxIMsVOtDD8klpVUj_vymbtZIDSkvS9-vjT5cQ2a9eA"
DEFAULT_SERVICE_ACCOUNT = "/etc/linkvideo-vpnsync/google_sheets_service_account.json"
DEFAULT_ENV_FILE = "/etc/linkvideo-vpnsync/vpnsync.env"

WORKING_SHEETS = (
    ("LV vpn01", "vpn01.linkvideo.ru", "Россия", 2500),
    ("LV vpn02", "vpn02.linkvideo.ru", "Россия", 1500),
    ("LV vpn03", "vpn03.linkvideo.ru", "Россия", 1500),
    ("LV vpn04", "vpn04.linkvideo.ru", "Россия", 1500),
    ("LV vpn05", "vpn05.linkvideo.ru", "Россия", 1500),
    ("LV vpn06", "vpn06.linkvideo.ru", "Россия", 1500),
    ("LV vpn07", "vpn07.linkvideo.ru", "Россия", 1500),
    ("LV vpn08", "vpn08.linkvideo.ru", "Россия", 1500),
    ("LV vpn09", "vpn09.linkvideo.ru", "Россия", 1500),
    ("LV vpn10", "vpn10.linkvideo.ru", "Россия", 1500),
    ("LV rb-vpn01", "rb-vpn01.linkvideo.ru", "Беларусь", 1500),
    ("LV kz-vpn01", "kz-vpn01.linkvideo.ru", "Казахстан", 1500),
)

LIFECYCLE_MAP = {
    "Активная": "active",
    "Спящая 30+": "sleeping",
    "Карантин 90+": "quarantine",
    "Кандидат в архив 365+": "archive_due",
    "Отключена вручную": "manual_disabled",
    "Последняя активность неизвестна": "never_active",
}


class SheetsReader:
    SCOPES = ("https://www.googleapis.com/auth/spreadsheets.readonly",)

    def __init__(self, service_account_file: str, spreadsheet_id: str) -> None:
        path = Path(service_account_file)
        if not path.is_file():
            raise FileNotFoundError(f"Google service-account file not found: {path}")
        self.credentials = Credentials.from_service_account_file(str(path), scopes=list(self.SCOPES))
        self.spreadsheet_id = spreadsheet_id
        self.session = requests.Session()

    def _token(self) -> str:
        if not self.credentials.valid or not self.credentials.token:
            self.credentials.refresh(Request())
        return str(self.credentials.token)

    def values(self, a1_range: str) -> list[list[Any]]:
        encoded = quote(a1_range, safe="")
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/{encoded}"
        response = self.session.get(
            url,
            params={"majorDimension": "ROWS", "valueRenderOption": "FORMATTED_VALUE"},
            headers={"Authorization": f"Bearer {self._token()}", "Accept": "application/json"},
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Google Sheets HTTP {response.status_code}: {response.text[:500]}")
        return list((response.json() or {}).get("values") or [])


def _rows(values: list[list[Any]]) -> list[dict[str, str]]:
    if not values:
        return []
    headers = [str(value or "").strip() for value in values[0]]
    result: list[dict[str, str]] = []
    for raw in values[1:]:
        row = ["" if value is None else str(value) for value in raw]
        if len(row) < len(headers):
            row += [""] * (len(headers) - len(row))
        result.append(dict(zip(headers, row[: len(headers)])))
    return result


def _bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"да", "yes", "true", "1", "on"}


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"никогда", "never", "none", "null"}:
        return None
    if re.match(r"^jan/01/1970\b", text, re.I):
        return None
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.astimezone() if parsed.tzinfo else parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    except ValueError:
        pass
    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y %H:%M:%S",
        "%b/%d/%Y %H:%M:%S",
        "%b/%d/%Y %H:%M",
    ):
        try:
            parsed = datetime.strptime(text, pattern)
            return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
        except ValueError:
            continue
    return None


def _clean_comment(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\|LV(?:1|2)\|(?:[^|]*\|)+", "", text, flags=re.I)
    return text.strip(" |")


def _snapshot(value: Any) -> tuple[dict[str, Any], str]:
    text = str(value or "").strip()
    if not text:
        return {}, ""
    try:
        data = json.loads(text)
        return (data if isinstance(data, dict) else {}), text
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}, text


def _single_ports(value: Any) -> list[int]:
    text = str(value or "").strip()
    if not text:
        return []
    result: list[int] = []
    for token in re.split(r"[,;\s]+", text):
        token = token.strip()
        if not token:
            continue
        if token.isdigit():
            number = int(token)
            if 1 <= number <= 65535:
                result.append(number)
            continue
        match = re.fullmatch(r"(\d+)-(\d+)", token)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if 1 <= start <= end <= 65535 and end - start <= 128:
                result.extend(range(start, end + 1))
    return result


def _ports(snapshot: dict[str, Any], visible: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    rules = snapshot.get("nat_rules")
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            external = _single_ports(rule.get("dst-port") or rule.get("dst_port"))
            internal = _single_ports(rule.get("to-ports") or rule.get("to_ports"))
            protocol = str(rule.get("protocol") or "tcp").strip().lower() or "tcp"
            for index, port in enumerate(external):
                key = (protocol, port)
                if key in seen:
                    continue
                seen.add(key)
                internal_port = internal[index] if index < len(internal) else (internal[0] if internal else port)
                result.append({
                    "external_port": port,
                    "internal_port": internal_port,
                    "protocol": protocol,
                    "to_address": str(rule.get("to-addresses") or rule.get("to_address") or ""),
                    "disabled": _bool(rule.get("disabled")),
                    "routeros_rule_id": str(rule.get(".id") or rule.get("id") or ""),
                })
    if result:
        return result

    for port in _single_ports(str(visible or "").replace("[off]", "")):
        key = ("tcp", port)
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "external_port": port,
            "internal_port": port,
            "protocol": "tcp",
            "to_address": "",
            "disabled": False,
            "routeros_rule_id": "",
        })
    return result


def _lifecycle(row: dict[str, str]) -> str:
    return LIFECYCLE_MAP.get(str(row.get("Lifecycle", "") or "").strip(), "unknown")


def _next_action(last_seen: datetime | None, first_seen: datetime | None) -> tuple[datetime | None, str | None]:
    now = datetime.now().astimezone()
    if last_seen is None:
        if first_seen is None:
            return None, None
        due = first_seen + timedelta(days=30)
        return (due if due > now else now), "delete_never_active"

    sleep_at = last_seen + timedelta(days=30)
    quarantine_at = last_seen + timedelta(days=90)
    delete_at = last_seen + timedelta(days=365)
    if now < sleep_at:
        return sleep_at, "sleep"
    if now < quarantine_at:
        return quarantine_at, "quarantine"
    if now < delete_at:
        return delete_at, "delete"
    return now, "delete"


def _server_country(host: str) -> str:
    host = str(host or "").lower()
    if host.startswith("kz-"):
        return "Казахстан"
    if host.startswith("rb-") or host.startswith("by-"):
        return "Беларусь"
    return "Россия"


def _safe_json(value: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"text": text}


def import_working(reader: SheetsReader, db: VPNDatabase) -> tuple[int, set[tuple[str, str]]]:
    imported = 0
    active_keys: set[tuple[str, str]] = set()
    for sheet, host, country, max_row in WORKING_SHEETS:
        values = reader.values(f"'{sheet}'!A1:T{max_row}")
        rows = _rows(values)
        server_id = db.ensure_server(host, country)
        count = 0
        for row in rows:
            login = str(row.get("Логин", "") or "").strip()
            if not login or _bool(row.get("Удалена")):
                continue
            snapshot, snapshot_text = _snapshot(row.get("RouterOS snapshot"))
            secret = snapshot.get("secret") if isinstance(snapshot.get("secret"), dict) else {}
            password = str(row.get("Пароль", "") or secret.get("password", "") or "")
            first_seen = _parse_dt(row.get("Первое обнаружение"))
            last_seen = _parse_dt(row.get("Последняя активность"))
            next_at, next_type = _next_action(last_seen, first_seen)
            client_id = db.upsert_client(
                server_id=server_id,
                login=login,
                remote_address=str(row.get("Remote Address", "") or ""),
                local_address=str(row.get("Local Address", "") or ""),
                profile=str(row.get("Profile", "") or secret.get("profile", "") or ""),
                service=str(row.get("Service", "") or secret.get("service", "") or ""),
                disabled=_bool(row.get("PPP disabled")),
                lifecycle_state=_lifecycle(row),
                last_seen_at=last_seen,
                first_seen_at=first_seen,
                next_action_at=next_at,
                next_action_type=next_type,
                password=password,
                recovery_snapshot=snapshot_text,
                routeros_comment=_clean_comment(row.get("Комментарий RouterOS")),
            )
            db.replace_nat_ports(server_id, client_id, _ports(snapshot, row.get("NAT / Порты")))
            db.remove_deleted_client(server_id, login)
            active_keys.add((host.lower(), login))
            count += 1
            imported += 1
        print(f"[IMPORT] {sheet}: {count} active clients")
    return imported, active_keys


def import_deleted(reader: SheetsReader, db: VPNDatabase, active_keys: set[tuple[str, str]]) -> tuple[int, int]:
    values = reader.values("'LV Удалённые'!A1:Q5000")
    rows = _rows(values)
    imported = 0
    skipped_active = 0
    for row in rows:
        host = str(row.get("VPN-сервер", "") or "").strip().lower()
        login = str(row.get("Логин", "") or "").strip()
        if not host or not login:
            continue
        if (host, login) in active_keys:
            skipped_active += 1
            continue
        server_id = db.ensure_server(host, _server_country(host))
        snapshot, snapshot_text = _snapshot(row.get("RouterOS snapshot"))
        secret = snapshot.get("secret") if isinstance(snapshot.get("secret"), dict) else {}
        db.upsert_deleted_client(
            server_id=server_id,
            login=login,
            remote_address=str(row.get("Remote Address", "") or ""),
            local_address=str(row.get("Local Address", "") or ""),
            profile=str(row.get("Profile", "") or secret.get("profile", "") or ""),
            service=str(row.get("Service", "") or secret.get("service", "") or ""),
            lifecycle_state=_lifecycle(row),
            last_seen_at=_parse_dt(row.get("Последняя активность")),
            first_seen_at=_parse_dt(row.get("Первое обнаружение")),
            deleted_at=_parse_dt(row.get("Удалена в")),
            deleted_reason=str(row.get("Причина", "") or ""),
            deleted_by=str(row.get("Кто удалил", "") or ""),
            source=str(row.get("Источник", "") or "google-sheets-migration"),
            password=str(row.get("Пароль", "") or secret.get("password", "") or ""),
            recovery_snapshot=snapshot_text,
            routeros_comment=_clean_comment(secret.get("comment", "")),
            ports=_ports(snapshot, row.get("NAT / Порты")),
        )
        imported += 1
    print(f"[IMPORT] LV Удалённые: {imported} archived clients; {skipped_active} stale archive rows skipped")
    return imported, skipped_active


def _history_event_id(row: dict[str, str]) -> str:
    operation = str(row.get("ID операции", "") or "").strip()
    raw = "\x1f".join([
        operation,
        str(row.get("Время", "") or ""),
        str(row.get("VPN-сервер", "") or ""),
        str(row.get("Логин", "") or ""),
        str(row.get("Действие", "") or ""),
        str(row.get("Что изменилось", "") or ""),
    ])
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]
    return f"sheets:{digest}"


def import_history(reader: SheetsReader, db: VPNDatabase, max_rows: int = 70000, chunk: int = 5000) -> int:
    header_values = reader.values("'LV История'!A1:K1")
    if not header_values:
        return 0
    headers = [str(value or "").strip() for value in header_values[0]]
    inserted_total = 0
    start = 2
    while start <= max_rows:
        end = min(max_rows, start + chunk - 1)
        block = reader.values(f"'LV История'!A{start}:K{end}")
        if not block:
            break
        rows: list[dict[str, str]] = []
        for raw in block:
            padded = ["" if value is None else str(value) for value in raw]
            if len(padded) < len(headers):
                padded += [""] * (len(headers) - len(padded))
            row = dict(zip(headers, padded[: len(headers)]))
            if any(str(value or "").strip() for value in row.values()):
                rows.append(row)
        if not rows:
            break

        server_ids: dict[str, int] = {}
        params: list[tuple[Any, ...]] = []
        for row in rows:
            host = str(row.get("VPN-сервер", "") or "").strip().lower()
            server_id = None
            if host:
                if host not in server_ids:
                    server_ids[host] = db.ensure_server(host, _server_country(host))
                server_id = server_ids[host]
            occurred = _parse_dt(row.get("Время")) or datetime.now().astimezone()
            action = str(row.get("Что произошло", "") or row.get("Действие", "") or "Изменение VPN")
            params.append((
                server_id,
                str(row.get("Логин", "") or ""),
                action,
                str(row.get("Что изменилось", "") or ""),
                json.dumps(_safe_json(row.get("Было")), ensure_ascii=False) if row.get("Было") else None,
                json.dumps(_safe_json(row.get("Стало")), ensure_ascii=False) if row.get("Стало") else None,
                str(row.get("Источник события", "") or "google-sheets-migration"),
                str(row.get("Кто изменил", "") or ""),
                _history_event_id(row),
                occurred,
            ))

        with db.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO vpn_change_log (
                    server_id, login, event_type, summary, old_value, new_value,
                    source, actor, external_event_id, created_at
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT (external_event_id) WHERE external_event_id <> '' DO NOTHING
                """,
                params,
            )
            inserted = cur.rowcount if cur.rowcount >= 0 else 0
            conn.commit()
            inserted_total += inserted
        print(f"[IMPORT] LV История rows {start}-{end}: {len(rows)} processed")
        if len(block) < chunk:
            break
        start = end + 1

    return inserted_total


def mark_import(db: VPNDatabase, source: str, details: dict[str, Any]) -> None:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO vpnsync_import_runs(source, imported_at, details)
            VALUES (%s, now(), %s::jsonb)
            ON CONFLICT (source) DO UPDATE
               SET imported_at = now(), details = EXCLUDED.details
            """,
            (source, json.dumps(details, ensure_ascii=False)),
        )
        conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import LinkVideo VPN Google Sheets into PostgreSQL")
    parser.add_argument("--service-account", default=DEFAULT_SERVICE_ACCOUNT)
    parser.add_argument("--spreadsheet-id", default=DEFAULT_SPREADSHEET_ID)
    parser.add_argument("--skip-history", action="store_true")
    args = parser.parse_args()

    load_dotenv(DEFAULT_ENV_FILE, override=False)
    settings = get_settings()
    reader = SheetsReader(args.service_account, args.spreadsheet_id)
    db = VPNDatabase(settings.database_url, settings.encryption_key)
    db.open()
    try:
        active_count, active_keys = import_working(reader, db)
        deleted_count, stale_count = import_deleted(reader, db, active_keys)
        history_count = 0 if args.skip_history else import_history(reader, db)
        details = {
            "spreadsheet_id": args.spreadsheet_id,
            "active_clients": active_count,
            "deleted_clients": deleted_count,
            "stale_deleted_skipped": stale_count,
            "history_inserted": history_count,
            "history_skipped": bool(args.skip_history),
        }
        mark_import(db, f"google-sheets:{args.spreadsheet_id}", details)
        print("[IMPORT] COMPLETE " + json.dumps(details, ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()
