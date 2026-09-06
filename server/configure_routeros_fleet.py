#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import grp
import json
import os
from pathlib import Path
from typing import Any

from linkvideo_vpnsync.routeros import RouterOSClient


ENV_FILE = Path("/etc/linkvideo-vpnsync/vpnsync.env")
ROUTERS_FILE = Path("/etc/linkvideo-vpnsync/routers.json")
SERVICE_GROUP = "linkvideo-vpnsync"

TARGETS = [
    *(f"vpn{i:02d}.linkvideo.ru" for i in range(1, 11)),
    "rb-vpn01.linkvideo.ru",
    "kz-vpn01.linkvideo.ru",
]


def _country(host: str) -> str:
    host = host.lower()
    if host.startswith("kz-"):
        return "Казахстан"
    if host.startswith("rb-") or host.startswith("by-"):
        return "Беларусь"
    return "Россия"


def _require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Run with sudo/root so /etc/linkvideo-vpnsync can be updated safely")


def _secure(path: Path) -> None:
    gid = grp.getgrnam(SERVICE_GROUP).gr_gid
    os.chown(path, 0, gid)
    os.chmod(path, 0o640)


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


def _read_counts(host: str, username: str, password: str, *, port: int, timeout: float) -> dict[str, Any]:
    with RouterOSClient(host, username, password, port=port, timeout=timeout) as api:
        secrets = api.print("/ppp/secret")
        active = api.print("/ppp/active")
        profiles = api.print("/ppp/profile")
        nat = api.print("/ip/firewall/nat")
        return {
            "port": int(api.connected_port or port),
            "secrets": len(secrets),
            "active": len(active),
            "profiles": len(profiles),
            "nat": len(nat),
        }


def _prompt_credentials(label: str, default_username: str = "", default_password: str = "") -> tuple[str, str]:
    suffix = f" [{default_username}]" if default_username else ""
    username = input(f"RouterOS username for {label}{suffix}: ").strip() or default_username
    if default_password:
        entered = getpass.getpass(f"RouterOS password for {label} [Enter = reuse previous]: ")
        password = entered or default_password
    else:
        password = getpass.getpass(f"RouterOS password for {label}: ")
    if not username or not password:
        raise SystemExit("Username/password cannot be empty")
    return username, password


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only validate the LinkVideo RouterOS fleet, then atomically enable VPNSync listeners"
    )
    parser.add_argument("--port", type=int, default=8728)
    parser.add_argument("--timeout", type=float, default=6.0)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="write successfully tested servers even when some targets fail (default: preserve current config on any failure)",
    )
    args = parser.parse_args()

    _require_root()
    print("[FLEET] All tests are read-only: /ppp/secret, /ppp/active, /ppp/profile, /ip/firewall/nat")
    print("[FLEET] No RouterOS set/enable/disable/remove command is used by this configurator.")

    common_user, common_password = _prompt_credentials("vpn01-vpn10")
    group_credentials: dict[str, tuple[str, str]] = {
        "Россия": (common_user, common_password),
        "Беларусь": (common_user, common_password),
        "Казахстан": (common_user, common_password),
    }

    successes: list[dict[str, Any]] = []
    failures: list[tuple[str, str]] = []

    for host in TARGETS:
        country = _country(host)
        username, password = group_credentials[country]
        print(f"[FLEET] Testing {host} ...", flush=True)
        try:
            counts = _read_counts(host, username, password, port=args.port, timeout=args.timeout)
        except Exception as first_exc:
            if country == "Россия":
                failures.append((host, str(first_exc)))
                print(f"[FLEET] FAILED {host}: {first_exc}", flush=True)
                continue

            print(f"[FLEET] {host} failed with common credentials: {first_exc}")
            answer = input(f"Retry {host} with separate {country} credentials? [y/N]: ").strip().lower()
            if answer not in {"y", "yes", "д", "да"}:
                failures.append((host, str(first_exc)))
                continue
            username, password = _prompt_credentials(host, common_user, common_password)
            group_credentials[country] = (username, password)
            try:
                counts = _read_counts(host, username, password, port=args.port, timeout=args.timeout)
            except Exception as retry_exc:
                failures.append((host, str(retry_exc)))
                print(f"[FLEET] FAILED {host}: {retry_exc}", flush=True)
                continue

        print(
            f"[FLEET] OK {host}: port={counts['port']} secrets={counts['secrets']} "
            f"active={counts['active']} profiles={counts['profiles']} nat={counts['nat']}",
            flush=True,
        )
        successes.append({
            "host": host,
            "country": country,
            "enabled": True,
            "username": username,
            "password": password,
            "port": counts["port"],
            "timeout": float(args.timeout),
        })

    print(f"[FLEET] Read-only validation complete: OK={len(successes)} FAILED={len(failures)}")
    for host, error in failures:
        print(f"[FLEET] FAILED {host}: {error}")

    if failures and not args.allow_partial:
        print("[FLEET] Existing routers.json was NOT changed because at least one target failed.")
        print("[FLEET] Fix/retry failed hosts, or explicitly use --allow-partial if partial rollout is intended.")
        raise SystemExit(2)
    if not successes:
        raise SystemExit("[FLEET] No RouterOS server passed validation; existing config preserved")

    payload = {"servers": successes}
    temporary = ROUTERS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _secure(temporary)
    os.replace(temporary, ROUTERS_FILE)
    _secure(ROUTERS_FILE)

    _update_env({
        "ROUTEROS_MONITOR_ENABLED": "true",
        "ROUTEROS_SERVERS_FILE": str(ROUTERS_FILE),
        "VPNSYNC_RETENTION_ENABLED": "false",
    })
    _secure(ENV_FILE)

    print(f"[FLEET] Configured {len(successes)} RouterOS servers; retention remains DISABLED.")
    print("[FLEET] No RouterOS write command was executed.")
    print("[FLEET] Restart explicitly when ready: sudo systemctl restart linkvideo-vpnsync")


if __name__ == "__main__":
    main()
