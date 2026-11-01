from __future__ import annotations

"""Offline OCI x64 detection and Oracle RADIUS tests; no live DB."""

import os
import struct
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

from linkvideo_vpn_helper.services.ula_x86_bridge import UlaX86BridgeGateway

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

    # The database username in Ula.ini is never an implicit employee login
    # when RADIUS is enabled.
    try:
        create_ula_gateway(details, "synthetic-only", config_dir=str(ula))
    except UlaReadError as exc:
        assert "личный логин" in str(exc)
    else:
        raise AssertionError("RADIUS must require the employee login explicitly")

    # Only a 32-bit OCI shipped with Ula: 64-bit Helper must fail safely.
    write_pe(ula / "oci.dll", 0x014c)
    assert _oci_architecture(ula / "oci.dll") == "x86"
    with patch.dict(os.environ, {"PATH": "", "ORACLE_HOME": ""}):
        assert find_oracle_client(ula) is None
        assert find_oracle_client(ula, architecture="x86") == ula.resolve()
        with patch(
            "linkvideo_vpn_helper.services.ula_x86_bridge.find_bridge_exe",
            return_value=root / "synthetic-sidecar.exe",
        ):
            gw_x86 = create_ula_gateway(
                details, "synthetic-only", config_dir=str(ula),
                login_username="employee",
            )
            assert isinstance(gw_x86, UlaX86BridgeGateway)
            gw_radius = create_ula_gateway(
                details, "employee-radius-password", config_dir=str(ula),
                login_username="employee", auth_mode="radius",
            )
            assert isinstance(gw_radius, UlaX86BridgeGateway)
            assert gw_radius._auth_mode == "radius"
            assert gw_radius._password == "employee-radius-password"
            gw_external = create_ula_gateway(
                details, "", config_dir=str(ula),
                login_username="employee", auth_mode="external",
            )
            assert isinstance(gw_external, UlaX86BridgeGateway)
            assert gw_external._password == ""
            assert gw_external._auth_mode == "external"
            with patch(
                "linkvideo_vpn_helper.services.ula_x86_bridge.run_bridge",
                return_value={"rows": []},
            ) as bridge:
                gw_x86._connect().close()
                assert bridge.call_args.args[0] == "test"
                assert bridge.call_args.kwargs["login"] == "employee"

    client = root / "existing-oracle-x64"
    client.mkdir()
    write_pe(client / "oci.dll", 0x8664)
    assert _oci_architecture(client / "oci.dll") == "x64"

    with patch.dict(os.environ, {"PATH": str(client), "ORACLE_HOME": ""}):
        assert find_oracle_client(ula) == client.resolve()
        with patch(
            "linkvideo_vpn_helper.services.ula_x86_bridge.find_bridge_exe",
            return_value=root / "synthetic-sidecar.exe",
        ):
            gw_explicit = create_ula_gateway(
                details, "synthetic-only", config_dir=str(ula),
                thick_client_dir=str(ula), login_username="employee",
            )
            assert isinstance(gw_explicit, UlaX86BridgeGateway)

        calls = []
        fake = types.ModuleType("oracledb")
        fake.DB_TYPE_CURSOR = "REF_CURSOR"
        fake.init_oracle_client = lambda **kw: calls.append(("init", kw))
        fake.makedsn = lambda host, port, *, sid=None, service_name=None: (
            f"{host}/{port}/{sid or service_name}"
        )
        class FakeCursor:
            def execute(self, statement):
                assert "AUTHENTICATED_IDENTITY" in statement
            def fetchone(self):
                return ("employee", "TECHNICAL")
            def close(self):
                return None
        def fake_connect(**kw):
            calls.append(("connect", {
                "user": kw.get("user"), "dsn": kw["dsn"],
                "has_password": bool(kw.get("password")),
                "external": kw.get("externalauth", False),
            }))
            return types.SimpleNamespace(
                close=lambda: None, autocommit=None, call_timeout=None,
                cursor=lambda: FakeCursor(),
            )
        fake.connect = fake_connect
        with patch.dict(sys.modules, {"oracledb": fake}):
            gw = create_ula_gateway(
                details, "synthetic-only", config_dir=str(ula),
                login_username="employee",
            )
            assert calls[0][0] == "init"
            assert calls[0][1]["lib_dir"] == str(client.resolve())
            assert calls[0][1]["config_dir"] == str(ula.resolve())
            conn = gw._connect()
            assert calls[1] == ("connect", {
                "user": "employee", "dsn": "127.0.0.1/1521/TEST",
                "has_password": True, "external": False,
            })
            conn.close()

            radius_login = create_ula_gateway(
                details, "employee-radius-password", config_dir=str(ula),
                login_username="employee", auth_mode="radius",
            )
            radius_login._connect().close()
            assert calls[-1] == ("connect", {
                "user": "employee", "dsn": "127.0.0.1/1521/TEST",
                "has_password": True, "external": False,
            })
            external = create_ula_gateway(
                details, "", config_dir=str(ula),
                login_username="employee", auth_mode="external",
            )
            external._connect().close()
            assert calls[-1] == ("connect", {
                "user": None, "dsn": "127.0.0.1/1521/TEST",
                "has_password": False, "external": True,
            })

    (ula / "sqlnet.ora").write_text("SQLNET.AUTHENTICATION_SERVICES=(NONE)\n")
    assert not ula_radius_configured(ula)
    (client / "oci.dll").write_bytes(b"not a PE file")
    assert _oci_architecture(client / "oci.dll") == "unknown"

print("CORE TESTS 3.0.16 ULA RADIUS OCI AUTO-DISCOVERY OK")
