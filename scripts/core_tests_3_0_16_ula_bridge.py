from __future__ import annotations

"""Offline checks for x86 Oracle subprocess protocol; no real credentials."""

import json
import struct
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from linkvideo_vpn_helper.services.ula_connection import UlaConnectionDetails
from linkvideo_vpn_helper.services.ula_oracle_gateway import UlaReadError
from linkvideo_vpn_helper.services.ula_x86_bridge import (
    UlaX86BridgeGateway, run_bridge,
)
from scripts.ula_x86_bridge import _safe_message, _perform
from linkvideo_vpn_helper.services.ula_connection import UlaIdentityError


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

    assert _safe_message(UlaIdentityError("private identity")) == "ULA-IDENTITY"

    # Test the actual worker with an offline fake Oracle client. External
    # authentication must not send employee credentials as AUTH_PASSWORD.
    oci = Path(root) / "oci.dll"
    header = bytearray(0x90)
    header[0:2] = b"MZ"
    struct.pack_into("<I", header, 0x3c, 0x80)
    header[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", header, 0x84, 0x014c)
    oci.write_bytes(header)
    (Path(root) / "sqlnet.ora").write_text(
        "SQLNET.AUTHENTICATION_SERVICES=(RADIUS)\n", encoding="ascii"
    )
    oracle_calls = []
    class DummyCursor:
        def execute(self, sql):
            assert "AUTHENTICATED_IDENTITY" in sql
        def fetchone(self):
            return ("employee", "TECHNICAL")
        def close(self):
            return None
    fake = types.ModuleType("cx_Oracle")
    fake.DB_TYPE_CURSOR = "CURSOR_TYPE"
    fake.init_oracle_client = lambda **kw: oracle_calls.append(("init", kw))
    fake.makedsn = lambda host, port, *, sid=None, service_name=None: (
        oracle_calls.append(("dsn", {"sid": sid, "service_name": service_name}))
        or "TEST_DSN"
    )
    def connect_fake(**kwargs):
        oracle_calls.append(("connect", kwargs))
        return SimpleNamespace(
            cursor=lambda: DummyCursor(),
            call_timeout=None, autocommit=None, close=lambda: None,
        )
    fake.connect = connect_fake
    request = {
        "action": "test",
        "auth_mode": "external",
        "details": {
            "host": "127.0.0.1", "port": 1521,
            "sid": "TEST", "username": "technical",
        },
        "login": "employee", "password": "",
        "client_dir": root, "config_dir": root,
    }
    with patch.dict(sys.modules, {"cx_Oracle": fake}):
        assert _perform(request) == {"ok": True, "rows": []}
    assert oracle_calls[1][1] == {"sid": None, "service_name": "TEST"}
    assert oracle_calls[2][1]["externalauth"] is True
    assert "user" not in oracle_calls[2][1]
    assert "password" not in oracle_calls[2][1]

    assert _safe_message(Exception("ORA-01017: secret data")) == "ORA-01017"
    assert _safe_message(Exception("sensitive connection details")) == (
        "Операция Ula завершилась ошибкой."
    )

print("CORE TESTS 3.0.16 ULA X86 BRIDGE IPC OK")
