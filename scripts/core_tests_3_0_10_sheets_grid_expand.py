from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linkvideo_vpn_helper.services.vpn_sheets_retention_compat import install_vpn_sheets_retention_compat

install_vpn_sheets_retention_compat()
from linkvideo_vpn_helper.services import vpn_sheets_sync as sheets


class FakeBackend(sheets.GoogleSheetsBackend):
    def __init__(self):
        self.spreadsheet_id = "test-sheet"
        self.requests = []
        self.value_ranges = []

    def _request(self, method, url, *, params=None, payload=None):
        self.requests.append((method, url, params, payload))
        if method == "GET" and url.endswith("/test-sheet"):
            return {
                "sheets": [{
                    "properties": {
                        "sheetId": 101,
                        "title": "LV vpn01",
                        "gridProperties": {"columnCount": 19},
                    }
                }]
            }
        return {}

    def get_values(self, a1_range):
        self.value_ranges.append(a1_range)
        return []


backend = FakeBackend()
rows = backend.read_server_rows("vpn01.linkvideo.ru")
assert rows == []

append_calls = [
    payload for method, url, params, payload in backend.requests
    if method == "POST" and url.endswith("/test-sheet:batchUpdate")
]
assert len(append_calls) == 1, backend.requests
append = append_calls[0]["requests"][0]["appendDimension"]
assert append == {"sheetId": 101, "dimension": "COLUMNS", "length": 1}, append
assert backend.value_ranges == ["'LV vpn01'!A2:T1500"]

# The successful expansion is cached, so writing the same tab does not issue a
# second metadata/batchUpdate cycle.
backend.write_server_rows("vpn01.linkvideo.ru", [], 0)
append_calls = [
    payload for method, url, params, payload in backend.requests
    if method == "POST" and url.endswith("/test-sheet:batchUpdate")
]
assert len(append_calls) == 1, backend.requests

print("CORE TESTS 3.0.10 GOOGLE SHEETS GRID EXPANSION OK")
