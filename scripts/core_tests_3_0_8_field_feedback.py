from __future__ import annotations

from pathlib import Path
import re
from types import SimpleNamespace

import linkvideo_vpn_helper.services.port_traffic_service as traffic_module
from linkvideo_vpn_helper.services.port_traffic_service import PortTrafficService
from linkvideo_vpn_helper.services import vpn_automation_service as automation_module
from linkvideo_vpn_helper.services import vpn_automation_service_core as automation_core


class AutomationAPI:
    def __init__(self):
        self.added: list[tuple[str, dict]] = []
        self.sets: list[tuple[str, str, dict]] = []

    def print(self, path, params=None):
        # New upsert code can re-read an object if an old API omitted ret. Our
        # fake always returns ret, so no persistent store is needed here.
        return []

    def add(self, path, params):
        params = dict(params)
        if path == "/system/logging/action":
            name = str(params.get("name", ""))
            assert re.fullmatch(r"[A-Za-z0-9]+", name), name
            # Functional minimum must be created before optional memory fields.
            assert set(params) <= {"name", "target"}, params
        if path == "/system/logging":
            assert params.get("action") == automation_module.LV_LOG_ACTION, params
            assert "regex" not in params, params
        self.added.append((path, params))
        return f"*{len(self.added)}"

    def set(self, path, rid, params):
        params = dict(params)
        self.sets.append((path, rid, params))
        # Simulate the terse real-server response. Each optional field is sent
        # alone, so Helper can safely skip only that field.
        optional_unsupported = {
            ("/system/script", "dont-require-permissions"),
            ("/system/logging/action", "memory-lines"),
            ("/system/logging/action", "memory-stop-on-full"),
            ("/system/logging", "regex"),
            ("/system/scheduler", "start-time"),
        }
        if len(params) == 1:
            field = next(iter(params))
            if (path, field) in optional_unsupported:
                raise RuntimeError("unknown parameter")

    def enable(self, path, rid):
        self.sets.append((path, rid, {"enabled": "yes"}))

    def disable(self, path, rid):
        self.sets.append((path, rid, {"enabled": "no"}))


class TrafficAPI:
    connection_queries: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def print(self, path, params=None):
        params = params or {}
        if path == "/ip/firewall/nat":
            return [
                {
                    ".id": "*N1",
                    "chain": "dstnat",
                    "protocol": "tcp",
                    "dst-port": "11136",
                    "to-addresses": "172.16.2.5",
                    "to-ports": "554",
                    "comment": "890000001",
                    "disabled": "no",
                },
                {
                    ".id": "*N2",
                    "chain": "dstnat",
                    "protocol": "tcp",
                    "dst-port": "11140",
                    "to-addresses": "172.16.2.5",
                    "to-ports": "554",
                    "comment": "890000001",
                    "disabled": "no",
                },
            ]
        if path != "/ip/firewall/connection":
            raise AssertionError(path)

        type(self).connection_queries.append(dict(params))
        assert "?reply-src-address=" not in params, params
        port = params.get("?dst-port=")
        if port == "11136":
            # Exact query + known NAT map must be sufficient even when this API
            # version omits reply-src-address from the returned row.
            return [
                {
                    ".id": "*C1",
                    "protocol": "tcp",
                    "dst-port": "11136",
                    "reply-src-port": "554",
                    "dstnat": "yes",
                    "seen-reply": "yes",
                    "orig-rate": "13.2kbps",
                    "repl-rate": "4.8Mbps",
                    "orig-bytes": "1000",
                    "repl-bytes": "9000",
                }
            ]
        if port == "11140":
            return [
                {
                    ".id": "*C2",
                    "protocol": "tcp",
                    "dst-port": "11140",
                    "reply-src-address": "172.16.2.5",
                    "reply-src-port": "554",
                    "dstnat": "yes",
                    "seen-reply": "yes",
                    "orig-rate": "0bps",
                    "repl-rate": "250kbps",
                    "orig-bytes": "500",
                    "repl-bytes": "5000",
                }
            ]
        return []


def main() -> None:
    assert automation_module.LV_LOG_ACTION == "LVAuth"
    assert re.fullmatch(r"[A-Za-z0-9]+", automation_module.LV_LOG_ACTION)
    assert automation_core.LV_LOG_ACTION == automation_module.LV_LOG_ACTION
    assert f'buffer="{automation_module.LV_LOG_ACTION}"' in automation_core.restore_script_source()

    # A server that rejects several optional fields with only "unknown
    # parameter" must still receive the complete functional LV component set.
    automation_api = AutomationAPI()
    automation_module.VPNAutomationService()._ensure_components(automation_api, preserve_pause=False)
    action_rows = [params for path, params in automation_api.added if path == "/system/logging/action"]
    assert action_rows and action_rows[0]["name"] == "LVAuth", automation_api.added
    logging_rows = [params for path, params in automation_api.added if path == "/system/logging"]
    assert len(logging_rows) == 2
    assert all(row.get("action") == "LVAuth" for row in logging_rows)
    assert len([1 for path, _params in automation_api.added if path == "/system/script"]) == 3
    assert len([1 for path, _params in automation_api.added if path == "/system/scheduler"]) == 3

    original_api = traffic_module.RouterOSAPIClient
    traffic_module.RouterOSAPIClient = TrafficAPI
    TrafficAPI.connection_queries.clear()
    try:
        samples = PortTrafficService().sample_client(
            "vpn05.linkvideo.ru",
            SimpleNamespace(username="u", password="p", port=8728, timeout=6.0),
            "890000001",
            "172.16.2.5",
            [11136, 11140],
        )
    finally:
        traffic_module.RouterOSAPIClient = original_api

    first = samples[11136]
    assert first.connections == 1, first
    assert first.seen_reply == 1
    assert first.internal_port == 554
    assert first.orig_rate_bps == 13_200, first.orig_rate_bps
    assert first.repl_rate_bps == 4_800_000, first.repl_rate_bps

    second = samples[11140]
    assert second.connections == 1, second
    assert second.internal_port == 554
    assert second.repl_rate_bps == 250_000, second.repl_rate_bps
    assert len(TrafficAPI.connection_queries) == 2, TrafficAPI.connection_queries

    root = Path(__file__).resolve().parents[1]
    visual = (root / "linkvideo_vpn_helper/ui/search_visual_fixes.py").read_text(encoding="utf-8")
    assert "Сервер: {client.server}" in visual
    assert "Страна: {country}" in visual
    assert "Портов: {len(client.ports)}" in visual
    assert "max(106, hinted.height() + 6)" in visual
    assert "font-weight: 600" in visual

    density = (root / "linkvideo_vpn_helper/ui/visual_density.py").read_text(encoding="utf-8")
    assert "geometry intentionally unchanged" in density
    assert "QFrame#SubtleCard" in density
    assert "QTableWidget" in density
    assert 'QLabel[pill="success"]' in density
    assert "QListWidget#PortList::item:selected" in density
    app = (root / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
    assert "install_visual_density()" in app

    print("CORE TESTS 3.0.8 FIELD FEEDBACK OK")


if __name__ == "__main__":
    main()
