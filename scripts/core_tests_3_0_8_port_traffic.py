from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import linkvideo_vpn_helper.services.port_traffic_service as traffic_module
from linkvideo_vpn_helper.services.port_traffic_service import PortTrafficService


class FakeAPI:
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
            # Never dump the complete conntrack table. The preferred query is
            # client-specific by translated Remote Address; a dst-port query is
            # allowed only as the compatibility fallback when that batch did not
            # contain the requested public port.
            assert params.get("?reply-src-address=") == "172.16.1.10" or params.get("?dst-port=") == "11312", params
            if params.get("?reply-src-address=") == "172.16.1.10":
                return [
                    {
                        ".id": "*C1",
                        "protocol": "tcp",
                        "dst-port": "11312",
                        "reply-src-address": "172.16.1.10",
                        "reply-src-port": "554",
                        "dstnat": "yes",
                        "seen-reply": "yes",
                        "orig-rate": "1200",
                        "repl-rate": "4800000",
                        "orig-bytes": "10000",
                        "repl-bytes": "9000000",
                    },
                    # Historical Helper treated a port number appearing in an
                    # arbitrary tuple field as active. Different dst-port must
                    # still be ignored even in a client-filtered result.
                    {
                        ".id": "*C3",
                        "protocol": "tcp",
                        "src-port": "11312",
                        "dst-port": "443",
                        "reply-src-address": "172.16.1.10",
                        "reply-src-port": "554",
                        "dstnat": "yes",
                        "seen-reply": "yes",
                        "repl-rate": "777777",
                    },
                ]
            return [
                # Same public port but another translated destination: must not
                # be attributed to the selected LinkVideo client by fallback.
                {
                    ".id": "*C2",
                    "protocol": "tcp",
                    "dst-port": "11312",
                    "reply-src-address": "172.16.1.99",
                    "reply-src-port": "554",
                    "dstnat": "yes",
                    "seen-reply": "yes",
                    "orig-rate": "999999",
                    "repl-rate": "999999",
                    "orig-bytes": "999999",
                    "repl-bytes": "999999",
                },
            ]
        raise AssertionError(path)


def main() -> None:
    original = traffic_module.RouterOSAPIClient
    traffic_module.RouterOSAPIClient = FakeAPI
    try:
        service = PortTrafficService()
        creds = SimpleNamespace(username="u", password="p", port=8728, timeout=1.0)
        samples = service.sample_client(
            "vpn01.example",
            creds,
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

    root = Path(__file__).resolve().parents[1]
    ui = (root / "linkvideo_vpn_helper/ui/port_traffic_inline.py").read_text(encoding="utf-8")
    assert "setItemWidget" in ui
    assert "setMinimumWidth(210)" in ui
    assert "● соединение · без трафика" in ui
    assert "○ нет соединения" in ui
    assert "setContentsMargins(10, 0, 10, 0)" in ui

    service_src = (root / "linkvideo_vpn_helper/services/port_traffic_service.py").read_text(encoding="utf-8")
    assert '"?reply-src-address=": remote' in service_src
    assert '"?dst-port=": str(port)' in service_src

    app = (root / "linkvideo_vpn_helper/app.py").read_text(encoding="utf-8")
    assert "install_inline_port_traffic()" in app

    print("CORE TESTS 3.0.8 INLINE PORT TRAFFIC OK")


if __name__ == "__main__":
    main()
