from __future__ import annotations

"""RADIUS + OCI Thick integration contract with synthetic local files only."""

import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

from linkvideo_vpn_helper.services.ula_connection import (
    UlaConnectionDetails, create_ula_gateway, ula_radius_configured,
)
from linkvideo_vpn_helper.services.ula_oracle_gateway import UlaReadError


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
    try:
        create_ula_gateway(details, "synthetic-only", config_dir=str(ula))
    except UlaReadError as exc:
        assert "RADIUS" in str(exc)
        assert "64-" in str(exc)
    else:
        raise AssertionError("Thin mode must be prohibited for Ula RADIUS auth")

    client = root / "instantclient_19_test"
    client.mkdir()
    (client / "oci.dll").write_bytes(b"MOCK-NO-EXECUTION")

    calls = []
    fake = types.ModuleType("oracledb")
    fake.DB_TYPE_CURSOR = "REF_CURSOR"
    fake.init_oracle_client = lambda **kw: calls.append(("init", kw))
    fake.makedsn = lambda host, port, *, sid: f"{host}/{port}/{sid}"
    fake.connect = lambda **kw: calls.append(("connect", {
        "user": kw["user"], "dsn": kw["dsn"],
        "has_password": bool(kw["password"]),
    })) or types.SimpleNamespace(
        close=lambda: None, autocommit=None, call_timeout=None
    )
    with patch.dict(sys.modules, {"oracledb": fake}):
        gw = create_ula_gateway(
            details, "synthetic-only",
            thick_client_dir=str(client), config_dir=str(ula),
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

print("CORE TESTS 3.0.16 ULA RADIUS CONFIG/OCI THICK OK")
