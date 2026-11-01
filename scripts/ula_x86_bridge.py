from __future__ import annotations

"""One-shot 32-bit Oracle RADIUS adapter for an installed Ula x86 OCI.

Only three operations are allowed. Credentials arrive through stdin, never
through arguments, files, environment variables or diagnostic output. This
binary is built by Windows CI using 32-bit Python/cx_Oracle and bundles no OCI.
"""

import json
import os
import re
import struct
import sys
from dataclasses import asdict
from pathlib import Path

from linkvideo_vpn_helper.services.ula_connection import (
    UlaConnectionDetails, _oci_architecture, check_external_identity,
)
from linkvideo_vpn_helper.services.ula_oracle_gateway import UlaOracleReadOnlyGateway


def _safe_message(exc: Exception) -> str:
    # Avoid raw cx_Oracle errors: they may include connection descriptors.
    code = re.search(r"\b(?:ORA|DPI)-\d{4,5}\b", str(exc))
    if code:
        return code.group(0)
    if isinstance(exc, (ValueError, TypeError)):
        return "Некорректные параметры запроса Ula."
    return "Операция Ula завершилась ошибкой."


def _read_request() -> dict:
    raw = sys.stdin.buffer.readline(65537)
    if not raw or len(raw) > 65536:
        raise ValueError("Invalid request size")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid request type")
    return value


def _perform(request: dict) -> dict:
    import cx_Oracle

    action = request.get("action")
    if action not in ("test", "find", "services"):
        raise ValueError("Unknown operation")
    details_raw = request.get("details")
    if not isinstance(details_raw, dict):
        raise ValueError("Missing Oracle config")
    # This field describes the endpoint; the user's own login is separate.
    details = UlaConnectionDetails(**details_raw).validated()
    login = request.get("login")
    password = request.get("password")
    auth_mode = request.get("auth_mode", "password")
    if auth_mode not in ("password", "external"):
        raise ValueError("Unknown authentication mode")
    client = request.get("client_dir")
    config = request.get("config_dir")
    if not isinstance(login, str) or not login.strip() or len(login) > 128:
        raise ValueError("Login is required")
    if auth_mode == "password" and (not isinstance(password, str) or not password):
        raise ValueError("Password is required")
    if not isinstance(client, str) or not isinstance(config, str):
        raise ValueError("Missing client config")
    dll_folder = Path(client).resolve()
    if _oci_architecture(dll_folder / "oci.dll") != "x86":
        raise ValueError("Incorrect OCI architecture")
    net_folder = Path(config).resolve()
    if not (net_folder / "sqlnet.ora").is_file():
        raise ValueError("Oracle Net configuration missing")
    # OCI may need adjacent DLLs; using the original installed Ula client
    # preserves that dependency chain without redistributing Oracle binaries.
    os.environ["PATH"] = str(dll_folder) + os.pathsep + os.environ.get("PATH", "")
    os.environ["TNS_ADMIN"] = str(net_folder)
    cx_Oracle.init_oracle_client(lib_dir=str(dll_folder), config_dir=str(net_folder))
    dsn = cx_Oracle.makedsn(details.host, details.port, sid=details.sid)

    def connect():
        if auth_mode == "external":
            connection = cx_Oracle.connect(
                dsn=dsn, externalauth=True, encoding="UTF-8",
                nencoding="UTF-8",
            )
        else:
            connection = cx_Oracle.connect(
                user=login.strip(), password=password, dsn=dsn,
                encoding="UTF-8", nencoding="UTF-8",
            )
        try:
            connection.call_timeout = 12000
            connection.autocommit = False
            if auth_mode == "external":
                check_external_identity(connection, login.strip())
            return connection
        except Exception:
            connection.close()
            raise

    if action == "test":
        connection = connect()
        try:
            return {"ok": True, "rows": []}
        finally:
            connection.close()

    gw = UlaOracleReadOnlyGateway(connect, cx_Oracle.DB_TYPE_CURSOR)
    if action == "find":
        hits = gw.search_clients(request.get("query", ""), search_type=request.get("search_type", 0))
        return {"ok": True, "rows": [asdict(hit) for hit in hits]}
    services = gw.list_services(
        request.get("client_id", ""),
        include_non_active=request.get("include_non_active", False),
        group_client=request.get("group_client", False),
    )
    return {"ok": True, "rows": [asdict(item) for item in services]}


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        # Test bitness and bundled Python driver without contacting Oracle.
        import cx_Oracle
        return 0 if (
            struct.calcsize("P") == 4
            and hasattr(cx_Oracle, "DB_TYPE_CURSOR")
        ) else 1
    if sys.argv[1:] != ["--stdio-once"]:
        return 2
    try:
        result = _perform(_read_request())
    except Exception as exc:
        result = {"ok": False, "error": _safe_message(exc)}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
