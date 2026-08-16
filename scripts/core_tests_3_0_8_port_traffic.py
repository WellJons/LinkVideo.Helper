from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import linkvideo_vpn_helper.services.port_traffic_service as traffic_module
from linkvideo_vpn_helper.services.port_traffic_service import PortTrafficService


class FakeAPI:
    connection_queries: list[dict] = []

    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.get("timeout")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def print(self, path, params=None):
        params = params or {}
        if path == "/ip/firewall/nat":
            return [
                {
                    ".id": "*1",
                    "chain": "dstnat",
                    "protocol": "tcp",
                    "dst-port": "11312",
                    "to-addresses": "172.16.1.10",
                    "to-ports": "554",
                    "comment": "89950000000",
                    "disabled": "no",
                }
            ]
        if path == "/ip/firewall/connection":
            type(self).connection_queries.append(dict(params))
            assert params.get("?dstnat=") == "yes", params
            return [
                {
                    ".id": "*C1",
                    "protocol": "tcp",
                    "dst-address": "203.0.113.10:11312",
                    "reply-src-address": "172.16.1.10:554",
                    "dstnat": "yes",
                    "seen-reply": "yes",
                    "orig-rate": "1200",
                    "repl-rate": "4800000",
                    "orig-bytes": "10000",
                    "repl-bytes": "9000000",
                },
                {
                    ".id": "*C2",
                    "protocol": "tcp",
                    "dst-address": "203.0.113.10:11399",
                    "reply-src-address": "172.16.1.10:554",
                    "dstnat": "yes",
                    "seen-reply": "yes",
                    "orig-rate": "999999",
                    "repl-rate": "999999",
                },
            ]
        raise AssertionError(path)


def main() -> None:
    original = traffic_module.RouterOSAPIClient
    traffic_module.RouterOSAPIClient = FakeAPI
    FakeAPI.connection_queries.clear()
    try:
        samples = PortTrafficService().sample_client(
            "vpn01.example",
            SimpleNamespace(username="u", password="p", port=8728, timeout=6.0),
            "89950000000",
            "172.16.1.10",
            [11312],
        )
    finally:
        traffic_module.RouterOSAPIClient = original

    sample = samples[11312]
    assert sample.internal_port == 554
    assert sample.connections == 1, sample
    assert sample.seen_reply == 1
    assert sample.orig_rate_bps == 1200
    assert sample.repl_rate_bps == 4_800_000
    assert sample.total_rate_bps == 4_801_200
    assert sample.orig_bytes == 10_000
    assert sample.repl_bytes == 9_000_000
    assert "DNAT snapshot: 2" in sample.diagnostic
    assert len(FakeAPI.connection_queries) == 1, FakeAPI.connection_queries

    root = Path(__file__).resolve().parents[1]
    ui = (root / "linkvideo_vpn_helper/ui/port_traffic_inline.py").read_text(encoding="utf-8")
    assert "setItemWidget" in ui
    assert "setMinimumWidth(210)" in ui
    assert "● соединение · без трафика" in ui
    assert "○ нет соединения" in ui
    assert "setInterval(5000)" in ui
    assert "ports," in ui
    assert "diagnostic" in ui

    service_src = (root / "linkvideo_vpn_helper/services/port_traffic_service.py").read_text(encoding="utf-8")
    assert '"?dstnat=": "yes"' in service_src
    assert "_connection_snapshot" in service_src
    assert "_endpoint_port" in service_src
    assert "fallback" in service_src
    assert "traffic_timeout = min(4.0" in service_src

    app = (root / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
    assert "install_inline_port_traffic()" in app

    print("CORE TESTS 3.0.8 INLINE PORT TRAFFIC OK")


if __name__ == "__main__":
    main()
