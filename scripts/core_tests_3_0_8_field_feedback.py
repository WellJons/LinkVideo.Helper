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

    def print(self, path, params=None):
        return []

    def add(self, path, params):
        params = dict(params)
        if path == "/system/logging/action":
            name = str(params.get("name", ""))
            assert re.fullmatch(r"[A-Za-z0-9]+", name), name
        if path == "/system/logging":
            assert params.get("action") == automation_module.LV_LOG_ACTION, params
        self.added.append((path, params))
        return f"*{len(self.added)}"

    def set(self, path, rid, params):
        raise AssertionError((path, rid, params))


class TrafficAPI:
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

        # Compatibility case seen on older RouterOS/API output: the query by
        # translated address works, but the queried field is omitted in the row.
        if params.get("?reply-src-address=") == "172.16.2.5":
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

        # A second port is not in the remote-address batch and must be recovered
        # by the exact public-port fallback.
        if params.get("?dst-port=") == "11140":
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
        if "?dst-port=" in params:
            return []
        return []


def main() -> None:
    # RouterOS error from the real VPN servers: logging action names may contain
    # letters/numbers only. Keep facade, inherited core logic and restore script
    # on exactly one canonical buffer name.
    assert automation_module.LV_LOG_ACTION == "LVAuth"
    assert re.fullmatch(r"[A-Za-z0-9]+", automation_module.LV_LOG_ACTION)
    assert automation_core.LV_LOG_ACTION == automation_module.LV_LOG_ACTION
    assert f'buffer="{automation_module.LV_LOG_ACTION}"' in automation_core.restore_script_source()

    automation_api = AutomationAPI()
    automation_module.VPNAutomationService()._ensure_components(automation_api, preserve_pause=False)
    action_rows = [params for path, params in automation_api.added if path == "/system/logging/action"]
    assert action_rows and action_rows[0]["name"] == "LVAuth", automation_api.added
    logging_rows = [params for path, params in automation_api.added if path == "/system/logging"]
    assert len(logging_rows) == 2
    assert all(row.get("action") == "LVAuth" for row in logging_rows)

    original_api = traffic_module.RouterOSAPIClient
    traffic_module.RouterOSAPIClient = TrafficAPI
    try:
        samples = PortTrafficService().sample_client(
            "vpn05.linkvideo.ru",
            SimpleNamespace(username="u", password="p", port=8728, timeout=1.0),
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

    root = Path(__file__).resolve().parents[1]
    visual = (root / "linkvideo_vpn_helper/ui/search_visual_fixes.py").read_text(encoding="utf-8")
    assert "Сервер: {client.server}" in visual
    assert "Страна: {country}" in visual
    assert "Портов: {len(client.ports)}" in visual
    assert "max(106, hinted.height() + 6)" in visual
    assert "font-weight: 600" in visual

    print("CORE TESTS 3.0.8 FIELD FEEDBACK OK")


if __name__ == "__main__":
    main()
