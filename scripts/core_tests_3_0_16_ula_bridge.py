from __future__ import annotations

"""Offline checks for x86 Oracle subprocess protocol; no real credentials."""

import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from linkvideo_vpn_helper.services.ula_connection import UlaConnectionDetails
from linkvideo_vpn_helper.services.ula_oracle_gateway import UlaReadError
from linkvideo_vpn_helper.services.ula_x86_bridge import (
    UlaX86BridgeGateway, run_bridge,
)
from scripts.ula_x86_bridge import _safe_message


with tempfile.TemporaryDirectory() as root:
    bridge_path = Path(root) / "LinkVideo.UlaBridge32.exe"
    bridge_path.write_bytes(b"FAKE-NOT-EXECUTED")
    details = UlaConnectionDetails("127.0.0.1", 1521, "TEST", "technical")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        req = json.loads(kwargs["input"])
        assert req["login"] == "employee", "service user must not become RADIUS login"
        assert req["password"] == "synthetic-secret"
        assert req["details"]["username"] == "technical"
        assert "synthetic-secret" not in str(argv)
        assert "employee" not in str(argv)
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 30
        if req["action"] == "find":
            rows = [{"client_id": "123", "name": "Test", "client_type": "J"}]
        elif req["action"] == "services":
            rows = [{
                "contract_service_id": "42", "service_name": "Video",
                "tariff_name": "Tariff", "costs_text": "100",
            }]
        else:
            rows = []
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "rows": rows}))

    with patch(
        "linkvideo_vpn_helper.services.ula_x86_bridge.find_bridge_exe",
        return_value=bridge_path,
    ), patch("linkvideo_vpn_helper.services.ula_x86_bridge.subprocess.run", fake_run):
        gw = UlaX86BridgeGateway(
            details=details, login="employee", password="synthetic-secret",
            client_dir=Path(root), config_dir=root,
        )
        gw._connect().close()
        assert gw.search_clients("Test")[0].client_id == "123"
        assert gw.list_services(
            "123", include_non_active=False, group_client=False
        )[0].costs_text == "100"
        assert len(calls) == 3

    with patch(
        "linkvideo_vpn_helper.services.ula_x86_bridge.find_bridge_exe",
        return_value=bridge_path,
    ), patch(
        "linkvideo_vpn_helper.services.ula_x86_bridge.subprocess.run",
        return_value=SimpleNamespace(
            returncode=0,
            stdout='{"ok":false,"error":"ORA-01017"}',
        ),
    ):
        try:
            run_bridge(
                "test", details=details, login="employee",
                password="synthetic-secret", client_dir=Path(root),
                config_dir=root,
            )
        except UlaReadError as exc:
            assert str(exc) == "ORA-01017"
        else:
            raise AssertionError("Worker authentication error was ignored")

    assert _safe_message(Exception("ORA-01017: secret data")) == "ORA-01017"
    assert _safe_message(Exception("sensitive connection details")) == (
        "Операция Ula завершилась ошибкой."
    )

print("CORE TESTS 3.0.16 ULA X86 BRIDGE IPC OK")
