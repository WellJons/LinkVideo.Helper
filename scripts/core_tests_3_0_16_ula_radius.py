from __future__ import annotations

"""Offline OCI x64 detection and Oracle RADIUS tests; no live DB."""

import os
import struct
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

from linkvideo_vpn_helper.services.ula_connection import (
    UlaConnectionDetails, _oci_architecture, create_ula_gateway,
    find_oracle_client, ula_radius_configured,
)
from linkvideo_vpn_helper.services.ula_oracle_gateway import UlaReadError


def write_pe(path: Path, machine: int):
    """Synthetic PE header: never loaded as a DLL."""
    data = bytearray(0x90)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 0x80)
    data[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", data, 0x84, machine)
    path.write_bytes(data)


details = UlaConnectionDetails("127.0.0.1", 1521, "TEST", "operator")
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    ula = root / "Ula"
    ula.mkdir()
    (ula / "Ula.ini").write_text(
        "[DB]\nServer=127.0.0.1\nPort=1521\nSid=TEST\nUserName=operator\n",
        encoding="cp1251",
    )
    (ula / "sqlnet.ora").write_text(
        "SQLNET.AUTHENTICATION_SERVICES=(RADIUS)\n", encoding="ascii",
    )
    assert ula_radius_configured(ula)

    # Only a 32-bit OCI shipped with Ula: 64-bit Helper must fail safely.
    write_pe(ula / "oci.dll", 0x014c)
    assert _oci_architecture(ula / "oci.dll") == "x86"
    with patch.dict(os.environ, {"PATH": "", "ORACLE_HOME": ""}):
        assert find_oracle_client(ula) is None
        try:
            create_ula_gateway(details, "synthetic-only", config_dir=str(ula))
        except UlaReadError as exc:
            assert "RADIUS" in str(exc) and "x64" in str(exc)
        else:
            raise AssertionError("RADIUS with x86-only OCI must not use Thin")

    client = root / "existing-oracle-x64"
    client.mkdir()
    write_pe(client / "oci.dll", 0x8664)
    assert _oci_architecture(client / "oci.dll") == "x64"

    with patch.dict(os.environ, {"PATH": str(client), "ORACLE_HOME": ""}):
        assert find_oracle_client(ula) == client.resolve()
        try:
            create_ula_gateway(
                details, "synthetic-only", config_dir=str(ula),
                thick_client_dir=str(ula),
            )
        except UlaReadError as exc:
            assert "x64" in str(exc)
        else:
            raise AssertionError("An explicitly selected x86 OCI must be rejected")

        calls = []
        fake = types.ModuleType("oracledb")
        fake.DB_TYPE_CURSOR = "REF_CURSOR"
        fake.init_oracle_client = lambda **kw: calls.append(("init", kw))
        fake.makedsn = lambda host, port, *, sid: f"{host}/{port}/{sid}"
        fake.connect = lambda **kw: calls.append(("connect", {
            "user": kw["user"], "dsn": kw["dsn"],
            "has_password": bool(kw["password"]),
        })) or types.SimpleNamespace(
            close=lambda: None, autocommit=None, call_timeout=None,
        )
        with patch.dict(sys.modules, {"oracledb": fake}):
            gw = create_ula_gateway(
                details, "synthetic-only", config_dir=str(ula),
            )
            assert calls[0][0] == "init"
            assert calls[0][1]["lib_dir"] == str(client.resolve())
            assert calls[0][1]["config_dir"] == str(ula.resolve())
            conn = gw._connect()
            assert calls[1] == ("connect", {
                "user": "operator", "dsn": "127.0.0.1/1521/TEST",
                "has_password": True,
            })
            conn.close()

    (ula / "sqlnet.ora").write_text("SQLNET.AUTHENTICATION_SERVICES=(NONE)\n")
    assert not ula_radius_configured(ula)
    (client / "oci.dll").write_bytes(b"not a PE file")
    assert _oci_architecture(client / "oci.dll") == "unknown"

print("CORE TESTS 3.0.16 ULA RADIUS OCI AUTO-DISCOVERY OK")
