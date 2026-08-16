from __future__ import annotations

from types import SimpleNamespace

import linkvideo_vpn_helper.services.port_traffic_service as traffic_module
from linkvideo_vpn_helper.services.port_traffic_service import PortTrafficService
from linkvideo_vpn_helper.services import vpn_automation_service as automation_module


class LegacyTrafficAPI:
    queries: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def print(self, path, params=None):
        params = dict(params or {})
        if path == "/ip/firewall/nat":
            return [
                {
                    ".id": "*N1",
                    "chain": "dstnat",
                    "protocol": "tcp",
                    "dst-port": "11158",
                    "to-addresses": "172.16.2.5",
                    "to-ports": "554",
                    "comment": "890000001",
                    "disabled": "no",
                }
            ]
        if path != "/ip/firewall/connection":
            raise AssertionError(path)

        type(self).queries.append(params)

        # Modern split tuple is not available on this simulated old RouterOS.
        if params.get("?reply-src-address=") == "172.16.2.5" and params.get("?reply-src-port=") == "554":
            return []

        # Legacy RouterOS/API representation: endpoint including the port is one
        # exact property value. There are no separate dst-port/reply-src-port
        # properties in the returned row.
        if params.get("?reply-src-address=") == "172.16.2.5:554":
            return [
                {
                    ".id": "*C1",
                    "protocol": "tcp",
                    "src-address": "198.51.100.20:50123",
                    "dst-address": "203.0.113.5:11158",
                    "reply-src-address": "172.16.2.5:554",
                    "reply-dst-address": "198.51.100.20:50123",
                    "dstnat": "yes",
                    "seen-reply": "yes",
                    "tcp-state": "established",
                    "orig-rate": "18.4kbps",
                    "repl-rate": "5.2Mbps",
                    "orig-bytes": "123456",
                    "repl-bytes": "9876543",
                }
            ]

        if params.get("?dst-port=") == "11158":
            return []
        if params.get("?dstnat=") == "yes":
            raise AssertionError("legacy reply-endpoint query should find the connection before dstnat fallback")
        return []


class NoIdLoggingAPI:
    def __init__(self):
        self.rows: dict[str, list[dict]] = {
            "/system/scheduler": [],
            "/system/script": [],
            "/system/logging/action": [],
            "/system/logging": [],
        }
        self.seq = 0

    def print(self, path, params=None):
        # Deliberately omit .id from /system/logging even when an explicit
        # proplist asks for it. This mirrors the real server behaviour that
        # triggered "создал ..., но не вернул .id".
        return [dict(row) for row in self.rows.setdefault(path, [])]

    def add(self, path, params):
        self.seq += 1
        row = dict(params)
        if path != "/system/logging":
            row[".id"] = f"*{self.seq}"
        self.rows.setdefault(path, []).append(row)
        if path == "/system/logging":
            return ""
        return row[".id"]

    def set(self, path, rid, params):
        for row in self.rows.setdefault(path, []):
            if row.get(".id") == rid:
                row.update(dict(params))
                return
        raise AssertionError((path, rid, params))

    def enable(self, path, rid):
        self.set(path, rid, {"disabled": "no"})

    def disable(self, path, rid):
        self.set(path, rid, {"disabled": "yes"})


def main() -> None:
    original = traffic_module.RouterOSAPIClient
    traffic_module.RouterOSAPIClient = LegacyTrafficAPI
    LegacyTrafficAPI.queries.clear()
    try:
        sample = PortTrafficService().sample_client(
            "vpn05.linkvideo.ru",
            SimpleNamespace(username="u", password="p", port=8728, timeout=3.0),
            "890000001",
            "172.16.2.5",
            [11158],
        )[11158]
    finally:
        traffic_module.RouterOSAPIClient = original

    assert sample.internal_port == 554, sample
    assert sample.connections == 1, sample
    assert sample.seen_reply == 1, sample
    assert sample.orig_rate_bps == 18_400, sample
    assert sample.repl_rate_bps == 5_200_000, sample
    assert any(q.get("?reply-src-address=") == "172.16.2.5:554" for q in LegacyTrafficAPI.queries), LegacyTrafficAPI.queries

    api = NoIdLoggingAPI()
    service = automation_module.VPNAutomationService()
    service._ensure_components(api, preserve_pause=False)
    logging_rows = api.rows["/system/logging"]
    assert len(logging_rows) == 2, logging_rows
    assert {row.get("prefix") for row in logging_rows} == {"LV-AUTH-PPP", "LV-AUTH-L2TP"}
    assert all(row.get("action") == "LVAuth" for row in logging_rows)
    assert all(".id" not in row for row in logging_rows)

    print("CORE TESTS 3.0.8 LEGACY ROUTEROS API OK")


if __name__ == "__main__":
    main()
