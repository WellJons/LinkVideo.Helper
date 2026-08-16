from __future__ import annotations

"""Reliable Google Sheets transport for the VPN mirror.

The original sync used one 15-second request with no retry and fired four server
workers at once. A transient Sheets/API slowdown therefore turned a healthy full
sync into several ReadTimeout failures. This layer keeps RouterOS as the source of
truth while making the Google side deliberately conservative:

* bounded retry/backoff for transient GET/PUT/idempotent batch requests;
* 30-second read timeout and connection pooling;
* at most two simultaneous Google HTTP requests per Helper instance;
* one cached spreadsheet-grid lookup instead of one metadata request per server;
* automatic A:S -> A:T migration in one idempotent batch;
* chunked server-sheet writes;
* cached LV Summary row lookup;
* uncertain history-append timeout verification by Sync ID, preventing duplicate
  history rows when Google accepted the append but the HTTP response was lost.
"""

import random
import threading
import time
from typing import Any

from linkvideo_vpn_helper.services.app_logging import event


_INSTALLED = False
_RETRYABLE_HTTP = {408, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3
_WRITE_CHUNK_ROWS = 350


class GoogleSheetsTransientError(RuntimeError):
    """A bounded retryable Google transport failure."""


class GoogleSheetsUncertainWriteError(GoogleSheetsTransientError):
    """Google may have accepted a non-idempotent append before the response timed out."""


def is_transient_google_error(exc: BaseException) -> bool:
    if isinstance(exc, GoogleSheetsTransientError):
        return True
    text = str(exc or "").lower()
    return any(token in text for token in (
        "sheets.googleapis.com",
        "read timed out",
        "connect timeout",
        "connection aborted",
        "connection reset",
        "temporarily unavailable",
        "google sheets временно",
    ))


def friendly_google_error(exc: BaseException) -> str:
    if isinstance(exc, GoogleSheetsUncertainWriteError):
        return "Google Sheets не подтвердил запись истории после тайм-аута"
    if is_transient_google_error(exc):
        return "Google Sheets временно не отвечает после повторных попыток"
    text = str(exc or "Ошибка Google Sheets").replace("\n", " ").strip()
    return text[:220]


def _retry_delay(attempt: int, response=None) -> float:
    if response is not None:
        try:
            retry_after = float(response.headers.get("Retry-After", "") or 0)
            if retry_after > 0:
                return min(12.0, retry_after)
        except Exception:
            pass
    base = 0.8 * (2 ** max(0, attempt - 1))
    return min(8.0, base + random.uniform(0.05, 0.35))


def install_vpn_sheets_resilience() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    import requests
    import linkvideo_vpn_helper.services.vpn_sheets_sync as sheets
    from linkvideo_vpn_helper.services.vpn_retention_policy import parse_extended_comment

    backend_cls = sheets.GoogleSheetsBackend
    original_init = backend_cls.__init__
    original_build_current = sheets.build_current_clients

    def robust_init(self, service_account_info: dict[str, Any], spreadsheet_id: str = sheets.SPREADSHEET_ID, timeout: float = 15.0):
        original_init(self, service_account_info, spreadsheet_id, timeout)
        # 15 seconds was too aggressive on real customer networks. Keep a short
        # connect deadline but allow a completed Sheets operation time to answer.
        self._lv_connect_timeout = 6.0
        self._lv_read_timeout = max(30.0, float(timeout or 0.0))
        self._lv_http_local = threading.local()
        self._lv_http_limit = threading.BoundedSemaphore(2)
        self._lv_grid_lock = threading.RLock()
        self._lv_grid_cache = None
        self._lv_reason_headers_ready: set[str] = set()
        self._lv_summary_lock = threading.RLock()
        self._lv_summary_rows = None

    def http_session(self):
        session = getattr(self._lv_http_local, "session", None)
        if session is None:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=0)
            session.mount("https://", adapter)
            self._lv_http_local.session = session
        return session

    def invalidate_token(self) -> None:
        with self._token_lock:
            credentials = getattr(self, "_credentials", None)
            if credentials is not None:
                try:
                    credentials.token = None
                except Exception:
                    pass

    def robust_request(self, method: str, url: str, *, params=None, payload=None) -> dict[str, Any]:
        method = str(method or "GET").upper()
        append_request = method == "POST" and ":append" in url
        idempotent = method in {"GET", "PUT", "DELETE"} or url.endswith(":batchUpdate")
        last_exc: BaseException | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            headers = {
                "Authorization": f"Bearer {self._access_token()}",
                "Accept": "application/json",
            }
            if payload is not None:
                headers["Content-Type"] = "application/json; charset=utf-8"

            try:
                with self._lv_http_limit:
                    response = http_session(self).request(
                        method,
                        url,
                        params=params,
                        json=payload,
                        headers=headers,
                        timeout=(self._lv_connect_timeout, self._lv_read_timeout),
                    )
            except requests.ConnectTimeout as exc:
                last_exc = exc
                if attempt < _MAX_ATTEMPTS:
                    event("SHEETS", "Повтор Google-запроса", f"нет соединения · попытка {attempt + 1}/{_MAX_ATTEMPTS}")
                    time.sleep(_retry_delay(attempt))
                    continue
                raise GoogleSheetsTransientError(
                    f"Google Sheets: соединение не установлено после {_MAX_ATTEMPTS} попыток"
                ) from exc
            except requests.ReadTimeout as exc:
                last_exc = exc
                # Repeating an append blindly can duplicate LV History. Let the
                # append_history wrapper verify its Sync ID first.
                if append_request:
                    raise GoogleSheetsUncertainWriteError(
                        f"Google Sheets: ответ на добавление истории не получен за {self._lv_read_timeout:.0f} с"
                    ) from exc
                if idempotent and attempt < _MAX_ATTEMPTS:
                    event("SHEETS", "Повтор Google-запроса", f"тайм-аут чтения · попытка {attempt + 1}/{_MAX_ATTEMPTS}")
                    time.sleep(_retry_delay(attempt))
                    continue
                raise GoogleSheetsTransientError(
                    f"Google Sheets: ответ не получен после {_MAX_ATTEMPTS} попыток"
                ) from exc
            except requests.ConnectionError as exc:
                last_exc = exc
                if append_request:
                    raise GoogleSheetsUncertainWriteError("Google Sheets: соединение оборвалось при записи истории") from exc
                if idempotent and attempt < _MAX_ATTEMPTS:
                    event("SHEETS", "Повтор Google-запроса", f"обрыв соединения · попытка {attempt + 1}/{_MAX_ATTEMPTS}")
                    time.sleep(_retry_delay(attempt))
                    continue
                raise GoogleSheetsTransientError("Google Sheets: сетевое соединение недоступно") from exc

            status = int(response.status_code)
            if status == 401 and attempt < _MAX_ATTEMPTS:
                invalidate_token(self)
                event("SHEETS", "Обновляю Google-токен", f"попытка {attempt + 1}/{_MAX_ATTEMPTS}")
                time.sleep(0.2)
                continue

            if status in _RETRYABLE_HTTP:
                if attempt < _MAX_ATTEMPTS:
                    event("SHEETS", "Повтор Google-запроса", f"HTTP {status} · попытка {attempt + 1}/{_MAX_ATTEMPTS}")
                    time.sleep(_retry_delay(attempt, response))
                    continue
                raise GoogleSheetsTransientError(f"Google Sheets временно недоступен (HTTP {status})")

            if status >= 400:
                text = response.text.strip().replace("\n", " ")
                raise RuntimeError(f"Google Sheets HTTP {status}: {text[:360]}")
            if not response.content:
                return {}
            try:
                return response.json()
            except Exception as exc:
                raise RuntimeError("Google Sheets вернул некорректный JSON-ответ") from exc

        raise GoogleSheetsTransientError(f"Google Sheets: запрос не выполнен: {last_exc}")

    def _load_grid_cache(self) -> dict[str, tuple[int, int]]:
        with self._lv_grid_lock:
            if self._lv_grid_cache is not None:
                return self._lv_grid_cache
            base_url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}"
            metadata = self._request(
                "GET",
                base_url,
                params={"fields": "sheets(properties(sheetId,title,gridProperties(columnCount)))"},
            )
            cache: dict[str, tuple[int, int]] = {}
            for item in list(metadata.get("sheets") or []):
                props = dict(item.get("properties") or {})
                title = str(props.get("title", "") or "")
                sheet_id = props.get("sheetId")
                columns = int(dict(props.get("gridProperties") or {}).get("columnCount", 0) or 0)
                if title and sheet_id is not None:
                    cache[title] = (int(sheet_id), columns)
            self._lv_grid_cache = cache
            return cache

    def ensure_sheet_columns(self, sheet: str, minimum: int = 20) -> None:
        minimum = max(1, int(minimum))
        with self._lv_grid_lock:
            cache = _load_grid_cache(self)
            current = cache.get(str(sheet))
            if current is None:
                return
            sheet_id, columns = current
            if columns >= minimum:
                return
            base_url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}"
            # Setting columnCount is idempotent. If Google processed the request
            # but the HTTP response is lost, a retry cannot append extra columns.
            self._request(
                "POST",
                f"{base_url}:batchUpdate",
                payload={
                    "requests": [{
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": int(sheet_id),
                                "gridProperties": {"columnCount": minimum},
                            },
                            "fields": "gridProperties.columnCount",
                        }
                    }]
                },
            )
            cache[str(sheet)] = (sheet_id, minimum)
            event("SHEETS", "Расширен лист VPN", f"{sheet}: {columns} → {minimum} столбцов")

    def _summary_rows(self) -> dict[str, int]:
        with self._lv_summary_lock:
            if self._lv_summary_rows is not None:
                return self._lv_summary_rows
            values = self.get_values(f"'{sheets.SUMMARY_SHEET}'!A2:A60")
            mapping: dict[str, int] = {}
            for index, row in enumerate(values, start=2):
                host = str(row[0] if row else "").strip().lower()
                if host:
                    mapping[host] = index
            self._lv_summary_rows = mapping
            return mapping

    def prepare_sync(self, servers) -> None:
        """One bounded Google preflight before a multi-server desktop sync."""
        server_sheets = [sheets.sheet_for_server(host) for host in list(servers or [])]
        minimum = len(sheets.SERVER_COLUMNS)
        with self._lv_grid_lock:
            cache = _load_grid_cache(self)
            requests_payload = []
            expanded: list[tuple[str, int, int]] = []
            for title in server_sheets:
                current = cache.get(title)
                if current is None:
                    continue
                sheet_id, columns = current
                if columns < minimum:
                    requests_payload.append({
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": int(sheet_id),
                                "gridProperties": {"columnCount": minimum},
                            },
                            "fields": "gridProperties.columnCount",
                        }
                    })
                    expanded.append((title, sheet_id, columns))
            if requests_payload:
                base_url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}"
                self._request("POST", f"{base_url}:batchUpdate", payload={"requests": requests_payload})
                for title, sheet_id, old_columns in expanded:
                    cache[title] = (sheet_id, minimum)
                    event("SHEETS", "Расширен лист VPN", f"{title}: {old_columns} → {minimum} столбцов")

        # Put all T1 headers in one idempotent Values batch request instead of 12
        # individual calls. This is also a cheap connectivity check before RouterOS.
        pending_headers = [title for title in server_sheets if title not in self._lv_reason_headers_ready]
        if pending_headers:
            base_url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}"
            self._request(
                "POST",
                f"{base_url}/values:batchUpdate",
                payload={
                    "valueInputOption": "RAW",
                    "data": [
                        {"range": f"'{title}'!T1", "majorDimension": "ROWS", "values": [["Причина"]]}
                        for title in pending_headers
                    ],
                },
            )
            self._lv_reason_headers_ready.update(pending_headers)
        _summary_rows(self)

    def robust_write_server_rows(self, server: str, rows, previous_count: int) -> None:
        if len(rows) > sheets.MAX_SERVER_ROWS:
            raise RuntimeError(f"{server}: в лист не помещается {len(rows)} строк")
        sheet = sheets.sheet_for_server(server)
        self.ensure_sheet_columns(sheet, len(sheets.SERVER_COLUMNS))
        if sheet not in self._lv_reason_headers_ready:
            self.put_values(f"'{sheet}'!T1", [["Причина"]])
            self._lv_reason_headers_ready.add(sheet)

        encoded_rows = [sheets._dict_to_row(row) for row in rows]
        write_count = max(len(encoded_rows), int(previous_count))
        if write_count <= 0:
            return
        while len(encoded_rows) < write_count:
            encoded_rows.append([""] * len(sheets.SERVER_COLUMNS))

        # Smaller idempotent PUTs are markedly more reliable on slow links than
        # one multi-megabyte request containing every RouterOS snapshot.
        for offset in range(0, write_count, _WRITE_CHUNK_ROWS):
            chunk = encoded_rows[offset: offset + _WRITE_CHUNK_ROWS]
            start_row = 2 + offset
            end_row = start_row + len(chunk) - 1
            self.put_values(f"'{sheet}'!A{start_row}:T{end_row}", chunk)

    def robust_append_history(self, rows: list[list[str]]) -> None:
        if not rows:
            return
        sync_ids = {
            str(row[9] or "").strip()
            for row in rows
            if len(row) > 9 and str(row[9] or "").strip()
        }
        for attempt in range(2):
            try:
                self.append_values(f"'{sheets.HISTORY_SHEET}'!A:J", rows)
                return
            except GoogleSheetsUncertainWriteError as exc:
                # A ReadTimeout is ambiguous: the append may already be committed.
                # Verify Sync ID before any retry so LV History never gets doubled.
                try:
                    existing = self.get_values(f"'{sheets.HISTORY_SHEET}'!J2:J")
                    present = {str(row[0] if row else "").strip() for row in existing}
                except Exception as verify_exc:
                    raise GoogleSheetsTransientError(
                        "Google Sheets: не удалось подтвердить запись истории после тайм-аута"
                    ) from verify_exc
                if sync_ids and sync_ids.issubset(present):
                    event("SHEETS", "Подтверждена запись после тайм-аута", f"Sync ID: {', '.join(sorted(sync_ids))}")
                    return
                if attempt == 0:
                    event("SHEETS", "Повтор записи истории", "предыдущий append не найден по Sync ID")
                    time.sleep(1.0)
                    continue
                raise GoogleSheetsTransientError("Google Sheets: запись истории не подтверждена") from exc

    def robust_update_summary(self, server: str, synced_at: str, result) -> None:
        target_row = _summary_rows(self).get(str(server).strip().lower())
        if target_row is None:
            return
        counts = result.lifecycle_counts or {}
        self.put_values(
            f"'{sheets.SUMMARY_SHEET}'!C{target_row}:J{target_row}",
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

    def robust_build_current_clients(service, snapshot):
        current = original_build_current(service, snapshot)
        # vpn_sheets_sync predates LV2 and its legacy `has_lv` check only mentions
        # LV1. The retention wrapper already exposes the reason; make the lifecycle
        # column/counts authoritative from the compact LV2 state as well.
        for record in current:
            comment = str(record.row.get("Комментарий RouterOS", "") or "")
            if "|LV2|" not in comment:
                continue
            meta = parse_extended_comment(comment)
            state = meta.state if meta.state in sheets.LIFECYCLE_LABELS else record.lifecycle_code
            record.lifecycle_code = state
            record.row["Lifecycle"] = sheets.LIFECYCLE_LABELS.get(state, sheets.LIFECYCLE_LABELS["U"])
        return current

    backend_cls.__init__ = robust_init
    backend_cls._request = robust_request
    backend_cls.ensure_sheet_columns = ensure_sheet_columns
    backend_cls.prepare_sync = prepare_sync
    backend_cls.write_server_rows = robust_write_server_rows
    backend_cls.append_history = robust_append_history
    backend_cls.update_summary = robust_update_summary
    sheets.build_current_clients = robust_build_current_clients

    event(
        "SHEETS",
        "Надёжная синхронизация включена",
        "retry 3x · timeout 30с · Google concurrency 2 · cached schema/summary · chunked writes",
    )
    _INSTALLED = True
