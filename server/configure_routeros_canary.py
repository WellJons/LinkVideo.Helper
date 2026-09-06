#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import grp
import json
import os
from pathlib import Path

from linkvideo_vpnsync.routeros import RouterOSClient


ENV_FILE = Path("/etc/linkvideo-vpnsync/vpnsync.env")
ROUTERS_FILE = Path("/etc/linkvideo-vpnsync/routers.json")
SERVICE_GROUP = "linkvideo-vpnsync"


def _require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Run with sudo/root so /etc/linkvideo-vpnsync can be updated safely")


def _update_env(values: dict[str, str]) -> None:
    if not ENV_FILE.is_file():
        raise SystemExit(f"Missing {ENV_FILE}; install VPNSync first")
    original = ENV_FILE.read_text(encoding="utf-8").splitlines()
    keys = set(values)
    output: list[str] = []
    seen: set[str] = set()
    for line in original:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in keys:
            output.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in values.items():
        if key not in seen:
            output.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(output) + "\n", encoding="utf-8")


def _secure(path: Path) -> None:
    gid = grp.getgrnam(SERVICE_GROUP).gr_gid
    os.chown(path, 0, gid)
    os.chmod(path, 0o640)


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure one read-only RouterOS canary for LinkVideo.VPNSync")
    parser.add_argument("--host", default="vpn01.linkvideo.ru")
    parser.add_argument("--country", default="Россия")
    parser.add_argument("--port", type=int, default=8728)
    parser.add_argument("--timeout", type=float, default=6.0)
    args = parser.parse_args()

    _require_root()
    host = str(args.host).strip().lower()
    if not host:
        raise SystemExit("Host is empty")

    username = input(f"RouterOS username for {host}: ").strip()
    password = getpass.getpass(f"RouterOS password for {host}: ")
    if not username or not password:
        raise SystemExit("Username/password cannot be empty")

    print(f"[CANARY] Testing read-only RouterOS API access to {host} ...", flush=True)
    try:
        with RouterOSClient(host, username, password, port=args.port, timeout=args.timeout) as api:
            secrets = api.print("/ppp/secret")
            active = api.print("/ppp/active")
            profiles = api.print("/ppp/profile")
            nat = api.print("/ip/firewall/nat")
            connected_port = api.connected_port
    except Exception as exc:
        raise SystemExit(f"[CANARY] RouterOS read-only test FAILED: {exc}") from exc

    print(
        f"[CANARY] Read-only test OK: port={connected_port} "
        f"secrets={len(secrets)} active={len(active)} profiles={len(profiles)} nat={len(nat)}",
        flush=True,
    )

    payload = {
        "servers": [
            {
                "host": host,
                "country": str(args.country),
                "enabled": True,
                "username": username,
                "password": password,
                "port": int(connected_port or args.port),
                "timeout": float(args.timeout),
            }
        ]
    }
    ROUTERS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _secure(ROUTERS_FILE)

    _update_env({
        "ROUTEROS_MONITOR_ENABLED": "true",
        "ROUTEROS_SERVERS_FILE": str(ROUTERS_FILE),
        "VPNSYNC_RETENTION_ENABLED": "false",
    })
    _secure(ENV_FILE)

    print(f"[CANARY] Configured {host} only; retention remains DISABLED.")
    print("[CANARY] No RouterOS write command was executed.")
    print("[CANARY] Restart explicitly when ready: sudo systemctl restart linkvideo-vpnsync")


if __name__ == "__main__":
    main()
