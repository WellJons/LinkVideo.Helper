from __future__ import annotations

import getpass
import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from linkvideo_vpn_helper.services.vpn_lifecycle import classify_state, parse_lv_comment
from linkvideo_vpn_helper.services.vpn_service import SessionCredentials, VPNService


SPREADSHEET_ID = "1KxIMsVOtDD8klpVUj_vymbtZIDSkvS9-vjT5cQ2a9eA"
HISTORY_SHEET = "LV История"
SUMMARY_SHEET = "LV Сводка"
DELETED_SHEET = "LV Удалённые"
MAX_SERVER_ROWS = 1499
SYNC_INTERVAL_SECONDS = 300

SERVER_COLUMNS = (
    "Логин",
    "Пароль",
    "Service",
    "Profile",
    "Local Address",
    "Remote Address",
    "Комментарий RouterOS",
    "NAT / Порты",
    "Lifecycle",
    "PPP disabled",
    "Последняя активность",
    "Дней без связи",
    "Первое обнаружение",
    "Последнее изменение",
    "Удалена",
    "Удалена в",
    "Последняя сверка RouterOS",
    "Источник",
    "RouterOS snapshot",
)

HISTORY_COLUMNS = (
    "Время",
    "VPN-сервер",
    "Логин",
    "Действие",
    "Что изменилось",
    "Было",
    "Стало",
    "Источник события",
    "Кто изменил",
    "ID операции",
)

DELETED_COLUMNS = (
    "VPN-сервер",
    "Логин",
    "Пароль",
    "Service",
    "Profile",
    "Local Address",
    "Remote Address",
    "NAT / Порты",
    "Lifecycle",
    "Последняя активность",
    "Дней без связи",
    "Первое обнаружение",
    "Удалена в",
    "Причина",
    "Кто удалил",
    "Источник",
    "RouterOS snapshot",
)

# Только реальные изменения конфигурации/состояния создают запись аудита.
# Наблюдаемые поля «Последняя активность» и «Дней без связи» обновляются в
# серверном листе, но не превращают каждую 5-минутную сверку в новое событие.
COMPARE_COLUMNS = (
    "Логин",
    "Пароль",
    "Service",
    "Profile",
    "Local Address",
    "Remote Address",
    "Комментарий RouterOS",
    "NAT / Порты",
    "Lifecycle",
    "PPP disabled",
)
SENSITIVE_HISTORY_COLUMNS = {"Пароль", "RouterOS snapshot"}

LIFECYCLE_LABELS = {
    "A": "Активная",
    "S": "Спящая 30+",
    "Q": "Карантин 90+",
    "R": "Кандидат в архив 365+",
    "M": "Отключена вручную",
    "U": "Последняя активность неизвестна",
}


def sheet_for_server(server: str) -> str:
    host = str(server or "").strip().lower()
    short = host.split(".", 1)[0]
    return f"LV {short}"


def _yes(value: Any) -> bool:
    return str(value or "").strip().lower() in {"yes", "true", "1", "on", "да"}


def _now_text(now: datetime | None = None) -> str:
    return (now or datetime.now()).replace(microsecond=0).isoformat(sep=" ")


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row_to_dict(row: Iterable[Any]) -> dict[str, str]:
    values = ["" if value is None else str(value) for value in row]
    if len(values) < len(SERVER_COLUMNS):
        values += [""] * (len(SERVER_COLUMNS) - len(values))
    return dict(zip(SERVER_COLUMNS, values[: len(SERVER_COLUMNS)]))


def _dict_to_row(item: dict[str, Any]) -> list[str]:
    return ["" if item.get(name) is None else str(item.get(name, "")) for name in SERVER_COLUMNS]


def _history_values(item: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field_name in fields:
        if field_name in SENSITIVE_HISTORY_COLUMNS:
            result[field_name] = "<изменено>"
        else:
            result[field_name] = item.get(field_name, "")
    return result


def _history_summary(item: dict[str, Any], fields: list[str]) -> str:
    if not item or not fields:
        return "—"
    labels = {
        "Пароль": "Пароль",
        "Service": "Service",
        "Profile": "Профиль",
        "Local Address": "Local IP",
        "Remote Address": "Remote IP",
        "NAT / Порты": "Порты",
        "Lifecycle": "Состояние",
        "PPP disabled": "PPP",
        "Комментарий RouterOS": "Комментарий",
        "Причина": "Причина",
        "Удалена": "Удалена",
    }
    parts: list[str] = []
    for field_name in fields:
        if field_name == "RouterOS snapshot":
            continue
        if field_name == "Пароль":
            value = "<изменён>" if str(item.get(field_name, "") or "") else "—"
        elif field_name == "Комментарий RouterOS":
            value = parse_lv_comment(str(item.get(field_name, "") or "")).base_comment.strip() or "—"
        else:
            value = str(item.get(field_name, "") or "").strip() or "—"
        parts.append(f"{labels.get(field_name, field_name)}: {value}")
    return "; ".join(parts) if parts else "—"


def _compare_value(field_name: str, value: Any) -> str:
    text = str(value or "")
    if field_name == "Комментарий RouterOS":
        # LV-Activity меняет служебный last= внутри комментария. Это не ручное
        # изменение конфигурации, поэтому сравниваем только пользовательскую часть.
        return parse_lv_comment(text).base_comment.strip()
    return text


def _preserve_password_in_snapshot(snapshot_text: str, password: str) -> str:
    """Keep the last known PPP password if RouterOS hides it for this API user.

    Some RouterOS permission sets return /ppp/secret rows but omit the sensitive
    password field. A later successful sync must not destroy the only recovery
    copy that was already stored in Sheets.
    """
    secret_password = str(password or "")
    if not secret_password:
        return str(snapshot_text or "")
    try:
        payload = json.loads(str(snapshot_text or "") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return str(snapshot_text or "")
    if not isinstance(payload, dict):
        return str(snapshot_text or "")
    secret = payload.get("secret")
    if not isinstance(secret, dict):
        secret = {}
        payload["secret"] = secret
    if not str(secret.get("password", "") or ""):
        secret["password"] = secret_password
    return _safe_json(payload)


@dataclass(slots=True)
class CurrentClient:
    login: str
    row: dict[str, str]
    lifecycle_code: str


@dataclass(slots=True)
class ReconcileResult:
    rows: list[dict[str, str]]
    history: list[list[str]]
    archived: list[dict[str, str]] = field(default_factory=list)
    added: int = 0
    changed: int = 0
    deleted: int = 0
    restored: int = 0


@dataclass(slots=True)
class ServerSyncResult:
    server: str
    success: bool
    message: str
    clients: int = 0
    added: int = 0
    changed: int = 0
    deleted: int = 0
    restored: int = 0
    lifecycle_counts: dict[str, int] = field(default_factory=dict)


class GoogleSheetsBackend:
    """Минимальный Sheets REST-клиент.

    Приватный ключ сервисного аккаунта никогда не хранится в репозитории.
    Helper ищет его во внешнем JSON-файле/переменной окружения. Сам spreadsheet
    ID не является секретом и поэтому закреплён рядом со схемой синхронизации.
    """

    SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)

    def __init__(self, service_account_info: dict[str, Any], spreadsheet_id: str = SPREADSHEET_ID, timeout: float = 15.0):
        if not service_account_info:
            raise ValueError("Не заданы данные Google service account")
        self.service_account_info = dict(service_account_info)
        self.spreadsheet_id = str(spreadsheet_id or SPREADSHEET_ID).strip()
        self.timeout = max(3.0, float(timeout))
        self._credentials = None
        self._token_lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings=None) -> "GoogleSheetsBackend | None":
        raw_json = str(os.getenv("LINKVIDEO_SHEETS_SERVICE_ACCOUNT_JSON", "") or "").strip()
        if raw_json:
            try:
                return cls(json.loads(raw_json))
            except Exception:
                return None

        configured = ""
        if settings is not None:
            try:
                configured = str(settings.value("sheets/service_account_file", "", str) or "").strip()
            except Exception:
                configured = ""
        configured = str(os.getenv("LINKVIDEO_SHEETS_SERVICE_ACCOUNT_FILE", configured) or "").strip()

        candidates: list[Path] = []
        if configured:
            candidates.append(Path(os.path.expandvars(os.path.expanduser(configured))))
        program_data = str(os.getenv("PROGRAMDATA", "") or "").strip()
        if program_data:
            candidates.append(Path(program_data) / "LinkVideo" / "Helper" / "google_sheets_service_account.json")
        app_data = str(os.getenv("APPDATA", "") or "").strip()
        if app_data:
            candidates.append(Path(app_data) / "LinkVideo" / "Helper" / "google_sheets_service_account.json")

        for path in candidates:
            try:
                if path.is_file():
                    return cls(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return None

    def _access_token(self) -> str:
        with self._token_lock:
            from google.auth.transport.requests import Request
            from google.oauth2.service_account import Credentials

            if self._credentials is None:
                self._credentials = Credentials.from_service_account_info(
                    self.service_account_info,
                    scopes=list(self.SCOPES),
                )
            if not self._credentials.valid or not self._credentials.token:
                self._credentials.refresh(Request())
            return str(self._credentials.token)

    def _request(self, method: str, url: str, *, params=None, payload=None) -> dict[str, Any]:
        import requests

        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "Accept": "application/json",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        response = requests.request(
            method,
            url,
            params=params,
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            text = response.text.strip().replace("\n", " ")
            raise RuntimeError(f"Google Sheets HTTP {response.status_code}: {text[:500]}")
        if not response.content:
            return {}
        return response.json()

    def _values_url(self, a1_range: str, suffix: str = "") -> str:
        encoded = quote(a1_range, safe="")
        return f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/{encoded}{suffix}"

    def get_values(self, a1_range: str) -> list[list[Any]]:
        data = self._request("GET", self._values_url(a1_range), params={"majorDimension": "ROWS"})
        return list(data.get("values") or [])

    def put_values(self, a1_range: str, values: list[list[Any]]) -> None:
        self._request(
            "PUT",
            self._values_url(a1_range),
            params={"valueInputOption": "RAW"},
            payload={"range": a1_range, "majorDimension": "ROWS", "values": values},
        )

    def append_values(self, a1_range: str, values: list[list[Any]]) -> None:
        if not values:
            return
        self._request(
            "POST",
            self._values_url(a1_range, ":append"),
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            payload={"range": a1_range, "majorDimension": "ROWS", "values": values},
        )

    def ensure_auxiliary_sheets(self) -> None:
        lock = getattr(self, "_lv_schema_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._lv_schema_lock = lock
        with lock:
            if getattr(self, "_lv_aux_sheets_ready", False):
                return
            base_url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}"
            metadata = self._request(
                "GET",
                base_url,
                params={"fields": "sheets(properties(sheetId,title,gridProperties(columnCount,rowCount)))"},
            )
            titles = {
                str(dict(item.get("properties") or {}).get("title", "") or "")
                for item in list(metadata.get("sheets") or [])
            }
            requests_payload = []
            if HISTORY_SHEET not in titles:
                requests_payload.append({
                    "addSheet": {
                        "properties": {
                            "title": HISTORY_SHEET,
                            "gridProperties": {"rowCount": 5000, "columnCount": len(HISTORY_COLUMNS)},
                        }
                    }
                })
            if DELETED_SHEET not in titles:
                requests_payload.append({
                    "addSheet": {
                        "properties": {
                            "title": DELETED_SHEET,
                            "gridProperties": {"rowCount": 5000, "columnCount": len(DELETED_COLUMNS)},
                        }
                    }
                })
            if requests_payload:
                self._request("POST", f"{base_url}:batchUpdate", payload={"requests": requests_payload})
                if hasattr(self, "_lv_grid_cache"):
                    self._lv_grid_cache = None
            self.put_values(f"'{HISTORY_SHEET}'!A1:J1", [list(HISTORY_COLUMNS)])
            self.put_values(
                f"'{DELETED_SHEET}'!A1:Q1",
                [list(DELETED_COLUMNS)],
            )
            self._lv_aux_sheets_ready = True

    @staticmethod
    def _deleted_row_to_dict(row: Iterable[Any]) -> dict[str, str]:
        values = ["" if value is None else str(value) for value in row]
        if len(values) < len(DELETED_COLUMNS):
            values += [""] * (len(DELETED_COLUMNS) - len(values))
        return dict(zip(DELETED_COLUMNS, values[: len(DELETED_COLUMNS)]))

    @staticmethod
    def _deleted_dict_to_row(item: dict[str, Any]) -> list[str]:
        return ["" if item.get(name) is None else str(item.get(name, "")) for name in DELETED_COLUMNS]

    def read_server_rows(self, server: str) -> list[dict[str, str]]:
        sheet = sheet_for_server(server)
        values = self.get_values(f"'{sheet}'!A2:S1500")
        result: list[dict[str, str]] = []
        for row in values:
            item = _row_to_dict(row)
            if item.get("Логин", "").strip():
                result.append(item)
        return result

    def read_deleted_rows(self, server: str = "") -> list[dict[str, str]]:
        self.ensure_auxiliary_sheets()
        values = self.get_values(f"'{DELETED_SHEET}'!A2:Q5000")
        wanted_server = str(server or "").strip().lower()
        result: list[dict[str, str]] = []
        for raw in values:
            item = self._deleted_row_to_dict(raw)
            if not str(item.get("Логин", "") or "").strip():
                continue
            if wanted_server and str(item.get("VPN-сервер", "") or "").strip().lower() != wanted_server:
                continue
            result.append(item)
        return result

    def search_deleted_rows(self, query: str) -> list[dict[str, str]]:
        wanted = str(query or "").strip().lower()
        if not wanted:
            return []
        return [
            row for row in self.read_deleted_rows("")
            if wanted in str(row.get("Логин", "") or "").strip().lower()
        ]

    def find_deleted_row(self, server: str, login: str) -> dict[str, str] | None:
        wanted = str(login or "").strip()
        if not wanted:
            return None
        for row in self.read_deleted_rows(server):
            if str(row.get("Логин", "") or "").strip() == wanted:
                return row
        return None

    def archive_deleted_rows(self, server: str, rows: list[dict[str, str]]) -> None:
        if not rows:
            return
        self.ensure_auxiliary_sheets()
        lock = getattr(self, "_lv_deleted_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._lv_deleted_lock = lock
        with lock:
            existing = self.read_deleted_rows("")
            merged: dict[tuple[str, str], dict[str, str]] = {}
            for row in existing:
                key = (
                    str(row.get("VPN-сервер", "") or "").strip().lower(),
                    str(row.get("Логин", "") or "").strip(),
                )
                if key[0] and key[1]:
                    merged[key] = dict(row)
            for source_row in rows:
                row = dict(source_row)
                row["VPN-сервер"] = str(server or row.get("VPN-сервер", "") or "").strip()
                row["Кто удалил"] = str(row.get("Кто удалил", "") or row.get("Источник", "") or "RouterOS")
                key = (row["VPN-сервер"].lower(), str(row.get("Логин", "") or "").strip())
                if key[0] and key[1]:
                    merged[key] = row
            output = sorted(merged.values(), key=lambda row: (
                str(row.get("VPN-сервер", "") or "").lower(),
                str(row.get("Логин", "") or "").lower(),
            ))
            encoded = [self._deleted_dict_to_row(row) for row in output]
            previous_count = len(existing)
            write_count = max(len(encoded), previous_count)
            while len(encoded) < write_count:
                encoded.append([""] * len(DELETED_COLUMNS))
            if write_count:
                self.put_values(f"'{DELETED_SHEET}'!A2:Q{write_count + 1}", encoded)

    def remove_deleted_rows(self, server: str, logins: Iterable[str]) -> None:
        wanted = {str(login or "").strip() for login in logins if str(login or "").strip()}
        if not wanted:
            return
        self.ensure_auxiliary_sheets()
        lock = getattr(self, "_lv_deleted_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._lv_deleted_lock = lock
        with lock:
            existing = self.read_deleted_rows("")
            server_key = str(server or "").strip().lower()
            output = [
                row for row in existing
                if not (
                    str(row.get("VPN-сервер", "") or "").strip().lower() == server_key
                    and str(row.get("Логин", "") or "").strip() in wanted
                )
            ]
            encoded = [self._deleted_dict_to_row(row) for row in output]
            write_count = max(len(encoded), len(existing))
            while len(encoded) < write_count:
                encoded.append([""] * len(DELETED_COLUMNS))
            if write_count:
                self.put_values(f"'{DELETED_SHEET}'!A2:Q{write_count + 1}", encoded)

    def write_server_rows(self, server: str, rows: list[dict[str, str]], previous_count: int) -> None:
        if len(rows) > MAX_SERVER_ROWS:
            raise RuntimeError(f"{server}: в лист не помещается {len(rows)} строк")
        sheet = sheet_for_server(server)
        encoded_rows = [_dict_to_row(row) for row in rows]
        write_count = max(len(encoded_rows), int(previous_count))
        if write_count <= 0:
            return
        while len(encoded_rows) < write_count:
            encoded_rows.append([""] * len(SERVER_COLUMNS))
        self.put_values(f"'{sheet}'!A2:S{write_count + 1}", encoded_rows)

    def append_history(self, rows: list[list[str]]) -> None:
        self.append_values(f"'{HISTORY_SHEET}'!A:J", rows)

    def update_summary(self, server: str, synced_at: str, result: ServerSyncResult) -> None:
        values = self.get_values(f"'{SUMMARY_SHEET}'!A2:J60")
        target_row = None
        for index, row in enumerate(values, start=2):
            host = str(row[0] if row else "").strip().lower()
            if host == str(server).strip().lower():
                target_row = index
                break
        if target_row is None:
            return
        counts = result.lifecycle_counts or {}
        self.put_values(
            f"'{SUMMARY_SHEET}'!C{target_row}:J{target_row}",
            [[
                synced_at,
                "OK" if result.success else result.message[:120],
                result.clients,
                counts.get("A", 0),
                counts.get("S", 0),
                counts.get("Q", 0),
                counts.get("R", 0),
                counts.get("M", 0),
            ]],
        )


def build_current_clients(service: VPNService, snapshot: dict[str, list[dict]]) -> list[CurrentClient]:
    secrets = list(snapshot.get("secrets") or [])
    profiles = list(snapshot.get("profiles") or [])
    active_rows = list(snapshot.get("actives") or [])
    nat_rules = list(snapshot.get("nat_rules") or [])

    profile_by_name = {
        str(row.get("name", "") or "").strip(): row
        for row in profiles
        if str(row.get("name", "") or "").strip()
    }
    active_by_name = {
        str(row.get("name", "") or "").strip(): row
        for row in active_rows
        if str(row.get("name", "") or "").strip()
    }

    now = datetime.now()
    current: list[CurrentClient] = []
    for secret in secrets:
        login = str(secret.get("name", "") or "").strip()
        if not login:
            continue
        profile_name = str(secret.get("profile", "") or login).strip()
        profile = profile_by_name.get(profile_name, {})
        remote = service._normalize_ip(secret.get("remote-address", ""))
        if not remote:
            remote = service._normalize_ip(profile.get("remote-address", ""))
        local = service._normalize_ip(profile.get("local-address", ""))
        active = active_by_name.get(login)

        matching_nat: list[dict] = []
        for rule in nat_rules:
            comment = str(rule.get("comment", "") or "").strip()
            rule_remote = service._normalize_ip(service._get_rule_remote(rule))
            if comment == login or (remote and rule_remote == remote):
                matching_nat.append(rule)

        nat_parts: list[str] = []
        for rule in matching_nat:
            protocol = str(rule.get("protocol", "tcp") or "tcp").lower()
            ext = str(service._get_rule_external_port(rule) or "").strip()
            to_port = str(rule.get("to-ports", "") or rule.get("to_ports", "") or ext).strip()
            disabled_nat = " [off]" if service._is_rule_disabled(rule) else ""
            if ext:
                nat_parts.append(f"{protocol} {ext}→{to_port or ext}{disabled_nat}")
        nat_text = "; ".join(nat_parts)

        disabled = _yes(secret.get("disabled", "no"))
        comment = str(secret.get("comment", "") or "")
        meta = parse_lv_comment(comment)
        has_lv = "|LV1|" in comment
        raw_last = str(secret.get("last-logged-out", "") or secret.get("last_logged_out", "") or "").strip()
        fallback_dt = service.parse_router_datetime(raw_last)
        last_ns = int(meta.last_ns or 0)
        if active:
            state = "A"
            last_activity = now.replace(microsecond=0).isoformat(sep=" ")
            age_days: str | int = 0
        else:
            if has_lv:
                state = str(meta.state or "U")
            else:
                fallback_ns = int(fallback_dt.timestamp() * 1_000_000_000) if fallback_dt else 0
                state = classify_state(fallback_ns, disabled, False)
                if last_ns <= 0:
                    last_ns = fallback_ns
            if last_ns > 0:
                last_dt = datetime.fromtimestamp(last_ns / 1_000_000_000)
                last_activity = last_dt.replace(microsecond=0).isoformat(sep=" ")
                age_days = max(0, (now - last_dt).days)
            elif fallback_dt is not None:
                last_activity = fallback_dt.replace(microsecond=0).isoformat(sep=" ")
                age_days = max(0, (now - fallback_dt).days)
            else:
                last_activity = raw_last
                age_days = ""

        state = state if state in LIFECYCLE_LABELS else "U"
        snapshot_json = _safe_json({
            "secret": secret,
            "profile": profile,
            "active": active or {},
            "nat_rules": matching_nat,
        })
        row = {
            "Логин": login,
            "Пароль": str(secret.get("password", "") or ""),
            "Service": str(secret.get("service", "") or "l2tp"),
            "Profile": profile_name,
            "Local Address": local,
            "Remote Address": remote,
            "Комментарий RouterOS": comment,
            "NAT / Порты": nat_text,
            "Lifecycle": LIFECYCLE_LABELS[state],
            "PPP disabled": "Да" if disabled else "Нет",
            "Последняя активность": last_activity,
            "Дней без связи": str(age_days),
            "RouterOS snapshot": snapshot_json,
        }
        current.append(CurrentClient(login=login, row=row, lifecycle_code=state))
    return current


def reconcile_records(
    server: str,
    existing_rows: list[dict[str, str]],
    current_clients: list[CurrentClient],
    *,
    source: str,
    initiator: str,
    now: datetime | None = None,
    sync_id: str | None = None,
) -> ReconcileResult:
    now_text = _now_text(now)
    sync_id = sync_id or uuid.uuid4().hex[:12]
    existing = {
        str(row.get("Логин", "") or "").strip(): dict(row)
        for row in existing_rows
        if str(row.get("Логин", "") or "").strip()
    }
    current = {item.login: item for item in current_clients if item.login}

    output: list[dict[str, str]] = []
    history: list[list[str]] = []
    archived: list[dict[str, str]] = []
    added = changed = deleted = restored = 0

    def add_history(login: str, event: str, fields: list[str], before: dict[str, Any], after: dict[str, Any]):
        history.append([
            now_text,
            server,
            login,
            event,
            ", ".join(fields),
            _history_summary(before, fields),
            _history_summary(after, fields),
            source,
            initiator,
            sync_id,
        ])

    for login in sorted(current):
        item = current[login]
        previous = existing.get(login)
        row = dict(item.row)
        row["Первое обнаружение"] = (
            previous.get("Первое обнаружение", "") if previous else ""
        ) or now_text
        row["Удалена"] = "Нет"
        row["Удалена в"] = ""
        row["Последняя сверка RouterOS"] = now_text
        row["Источник"] = source

        if previous is None:
            row["Последнее изменение"] = now_text
            added += 1
            add_history(login, "Обнаружена на RouterOS", list(COMPARE_COLUMNS), {}, row)
        else:
            # RouterOS can legitimately omit sensitive fields when the API account
            # lacks the sensitive policy. Never overwrite a previously captured
            # recovery password with an empty value during an otherwise healthy sync.
            previous_password = str(previous.get("Пароль", "") or "")
            if not str(row.get("Пароль", "") or "") and previous_password:
                row["Пароль"] = previous_password
                row["RouterOS snapshot"] = _preserve_password_in_snapshot(
                    str(row.get("RouterOS snapshot", "") or ""),
                    previous_password,
                )

            was_deleted = str(previous.get("Удалена", "") or "").strip().lower() in {"да", "yes", "true", "1"}
            changed_fields = [
                name for name in COMPARE_COLUMNS
                if _compare_value(name, previous.get(name, "")) != _compare_value(name, row.get(name, ""))
            ]
            if was_deleted:
                restored += 1
                row["Последнее изменение"] = now_text
                add_history(login, "Восстановлена на RouterOS", changed_fields or ["Удалена"], previous, row)
            elif changed_fields:
                changed += 1
                row["Последнее изменение"] = now_text
                add_history(login, "Изменена", changed_fields, previous, row)
            else:
                row["Последнее изменение"] = previous.get("Последнее изменение", "") or now_text
        output.append(row)

    for login in sorted(set(existing) - set(current)):
        previous = dict(existing[login])
        was_deleted = str(previous.get("Удалена", "") or "").strip().lower() in {"да", "yes", "true", "1"}
        previous["Последняя сверка RouterOS"] = now_text
        if not was_deleted:
            before = dict(previous)
            previous["Удалена"] = "Да"
            previous["Удалена в"] = now_text
            previous["Последнее изменение"] = now_text
            previous["Источник"] = source
            deleted += 1
            add_history(login, "Удалена на RouterOS", ["Удалена"], before, previous)
        previous["VPN-сервер"] = server
        archived.append(previous)

    output.sort(key=lambda row: str(row.get("Логин", "") or "").lower())
    return ReconcileResult(output, history, archived, added, changed, deleted, restored)


class VPNSheetsSyncService:
    def __init__(self, vpn_service: VPNService, backend: GoogleSheetsBackend):
        self.vpn_service = vpn_service
        self.backend = backend

    @staticmethod
    def default_initiator() -> str:
        try:
            return getpass.getuser() or "LinkVideo.Helper"
        except Exception:
            return "LinkVideo.Helper"

    def sync_server(
        self,
        server: str,
        creds: SessionCredentials,
        *,
        source: str = "RouterOS sync",
        initiator: str = "",
    ) -> ServerSyncResult:
        # ВАЖНО: сначала должен полностью и без ошибки прочитаться RouterOS.
        # Если fetch_config_snapshot() упал, до reconciliation мы не доходим и
        # ни одна старая строка не может быть ошибочно помечена как удалённая.
        snapshot = self.vpn_service.fetch_config_snapshot(server, creds)
        current_clients = build_current_clients(self.vpn_service, snapshot)

        self.backend.ensure_auxiliary_sheets()
        existing_rows = self.backend.read_server_rows(server)
        sync_id = uuid.uuid4().hex[:12]
        result = reconcile_records(
            server,
            existing_rows,
            current_clients,
            source=source,
            initiator=initiator or self.default_initiator(),
            sync_id=sync_id,
        )

        # Журнал важнее текущего представления: если второй запрос к Google
        # внезапно не пройдёт, лучше получить повторное событие при следующей
        # сверке, чем навсегда потерять факт изменения.
        if result.archived:
            # Archive first. Only after Google confirms the recovery copy do we
            # remove the deleted rows from the per-server working sheet.
            self.backend.archive_deleted_rows(server, result.archived)
        if result.history:
            self.backend.append_history(result.history)
        self.backend.write_server_rows(server, result.rows, len(existing_rows))
        if result.added:
            try:
                self.backend.remove_deleted_rows(
                    server,
                    [item.login for item in current_clients],
                )
            except Exception:
                # The active RouterOS state is authoritative; stale archive rows
                # are harmless and can be cleaned on a later sync.
                pass

        counts: dict[str, int] = {key: 0 for key in LIFECYCLE_LABELS}
        for item in current_clients:
            counts[item.lifecycle_code] = counts.get(item.lifecycle_code, 0) + 1
        synced_at = _now_text()
        sync_result = ServerSyncResult(
            server=server,
            success=True,
            message="OK",
            clients=len(current_clients),
            added=result.added,
            changed=result.changed,
            deleted=result.deleted,
            restored=result.restored,
            lifecycle_counts=counts,
        )
        try:
            self.backend.update_summary(server, synced_at, sync_result)
        except Exception:
            # Сводка вторична. Ошибка её обновления не должна откатывать уже
            # записанный серверный лист и историю.
            pass
        return sync_result
