from __future__ import annotations

"""An isolated 32-bit Oracle/RADIUS bridge: stdin JSON request, stdout JSON result.

The bridge runs only when launched by LinkVideo.Helper; no socket listener and
no command-line secrets. The password is received over the child stdin pipe,
used for a single read-only operation, then discarded on process termination.
"""

import json
import re
import sys
from dataclasses import asdict
from pathlib import Path


def _process(request: dict):
    if sys.maxsize > 2**32:
        raise RuntimeError("BRIDGE_ARCH")
    from linkvideo_vpn_helper.services.ula_connection import (
        UlaConnectionDetails, _oci_architecture,
    )
    from linkvideo_vpn_helper.services.ula_oracle_gateway import (
        UlaOracleReadOnlyGateway,
    )

    if type(request) is not dict or request.get("protocol") != 1:
        raise ValueError("BAD_PROTOCOL")
    action = request.get("action")
    if action not in ("connect", "find", "services"):
        raise ValueError("BAD_ACTION")

    config_dir = Path(str(request["config_dir"])).resolve()
    lib_dir = Path(str(request["lib_dir"])).resolve()
    if _oci_architecture(lib_dir / "oci.dll") != "x86":
        raise ValueError("OCI_ARCH")
    if not (config_dir / "sqlnet.ora").is_file():
        raise ValueError("ORACLE_NET_CONFIG")

    details = UlaConnectionDetails(
        str(request["host"]), int(request["port"]),
        str(request["sid"]), str(request["username"]),
    ).validated()
    password = request.get("password")
    if not isinstance(password, str) or not password:
        raise ValueError("EMPTY_PASSWORD")

    import oracledb
    oracledb.init_oracle_client(
        lib_dir=str(lib_dir),
        config_dir=str(config_dir),
    )
    dsn = oracledb.makedsn(details.host, details.port, sid=details.sid)

    def connect():
        conn = oracledb.connect(
            user=details.username, password=password,
            dsn=dsn, tcp_connect_timeout=8,
        )
        conn.call_timeout = 12000
        conn.autocommit = False
        return conn

    if action == "connect":
        conn = connect()
        try:
            return True
        finally:
            conn.close()

    gateway = UlaOracleReadOnlyGateway(connect, oracledb.DB_TYPE_CURSOR)
    if action == "find":
        values = gateway.search_clients(
            request.get("search_text", ""),
            search_type=request.get("search_type", 0),
        )
    else:
        values = gateway.list_services(
            request.get("client_id", ""),
            include_non_active=request.get("include_non_active", False),
            group_client=request.get("group_client", False),
        )
    return [asdict(item) for item in values]


def main():
    # Do not echo a full exception: it may contain connection descriptions,
    # usernames, server addresses, user-supplied values or credentials.
    try:
        raw = sys.stdin.buffer.read(8192)
        if len(raw) >= 8192:
            raise ValueError("REQUEST_TOO_LARGE")
        request = json.loads(raw.decode("utf-8"))
        result = _process(request)
        response = {"ok": True, "result": result}
    except Exception as exc:
        code = re.search(r"\\b(?:ORA|DPI|DPY)-\\d{4,5}\\b", str(exc))
        response = {
            "ok": False,
            "error": code.group(0) if code else "BRIDGE_ERROR",
        }
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
