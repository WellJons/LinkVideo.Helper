from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests
from linkvideo_vpn_helper.services.vpn_sheets_retention_compat import install_vpn_sheets_retention_compat
import linkvideo_vpn_helper.services.vpn_sheets_resilience as resilience

install_vpn_sheets_retention_compat()
resilience.install_vpn_sheets_resilience()
from linkvideo_vpn_helper.services import vpn_sheets_sync as sheets

# Keep this regression fast; backoff timing itself is not the contract under test.
real_sleep = resilience.time.sleep
resilience.time.sleep = lambda _seconds: None


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = {} if payload is None else payload
        self.content = b"{}"
        self.text = "{}"
        self.headers = {}

    def json(self):
        return self._payload


class RetrySession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if len(self.calls) == 1:
            raise requests.ReadTimeout("slow Google")
        return FakeResponse(200, {"values": [["ok"]]})


try:
    backend = sheets.GoogleSheetsBackend({"client_email": "test@example.invalid"})
    backend._access_token = lambda: "token"
    retry_session = RetrySession()
    backend._lv_http_local.session = retry_session

    data = backend._request("GET", "https://sheets.googleapis.com/test")
    assert data == {"values": [["ok"]]}
    assert len(retry_session.calls) == 2, retry_session.calls
    assert retry_session.calls[0][2]["timeout"] == (6.0, 30.0)

    # An ambiguous append timeout must be verified by Sync ID instead of blindly
    # duplicating the same audit row.
    append_calls = []
    backend.append_values = lambda _range, _rows: (
        append_calls.append((_range, _rows)),
        (_ for _ in ()).throw(resilience.GoogleSheetsUncertainWriteError("unknown result")),
    )[1]
    backend.get_values = lambda _range: [["sync-123"]]
    history = [["2026-08-16", "vpn01", "u", "event", "", "", "", "src", "op", "sync-123"]]
    backend.append_history(history)
    assert len(append_calls) == 1, append_calls

    # Large server tabs are written in bounded idempotent chunks rather than one
    # giant request carrying hundreds of RouterOS snapshots.
    ranges = []
    backend.ensure_sheet_columns = lambda *_args, **_kwargs: None
    backend._lv_reason_headers_ready.add("LV vpn01")
    backend.put_values = lambda a1, values: ranges.append((a1, len(values)))
    rows = [{"Логин": f"user-{index}"} for index in range(701)]
    backend.write_server_rows("vpn01.linkvideo.ru", rows, 0)
    assert ranges == [
        ("'LV vpn01'!A2:T351", 350),
        ("'LV vpn01'!A352:T701", 350),
        ("'LV vpn01'!A702:T702", 1),
    ], ranges

    # Multi-server preflight reuses one cached grid snapshot, expands every legacy
    # A:S tab in one batch, and writes T1 headers in one Values batch request.
    backend._lv_grid_cache = {
        "LV vpn01": (101, 19),
        "LV vpn02": (102, 19),
    }
    backend._lv_reason_headers_ready.clear()
    backend._lv_summary_rows = {}
    requests_seen = []
    backend._request = lambda method, url, *, params=None, payload=None: (
        requests_seen.append((method, url, params, payload)) or {}
    )
    backend.prepare_sync(["vpn01.linkvideo.ru", "vpn02.linkvideo.ru"])
    sheet_updates = [
        call for call in requests_seen
        if call[0] == "POST" and call[1].endswith(":batchUpdate") and "/values:" not in call[1]
    ]
    header_updates = [
        call for call in requests_seen
        if call[0] == "POST" and call[1].endswith("/values:batchUpdate")
    ]
    assert len(sheet_updates) == 1, requests_seen
    assert len(sheet_updates[0][3]["requests"]) == 2
    assert len(header_updates) == 1, requests_seen
    assert len(header_updates[0][3]["data"]) == 2

    source = (ROOT / "linkvideo_vpn_helper/services/vpn_sheets_resilience.py").read_text(encoding="utf-8")
    for marker in (
        "_MAX_ATTEMPTS = 3",
        "BoundedSemaphore(2)",
        "_lv_read_timeout = max(30.0",
        "GoogleSheetsUncertainWriteError",
        "Sync ID",
        "_WRITE_CHUNK_ROWS = 350",
        "prepare_sync",
        "updateSheetProperties",
        'if "|LV2|" not in comment',
    ):
        assert marker in source, marker
finally:
    resilience.time.sleep = real_sleep

print("CORE TESTS 3.0.10 SHEETS RESILIENCE OK")
