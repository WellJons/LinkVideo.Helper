#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from linkvideo_vpnsync.config import get_settings
from linkvideo_vpnsync.db import VPNDatabase
from linkvideo_vpnsync.monitor import load_targets
from linkvideo_vpnsync.routeros import RouterOSClient


LEGACY_SCHEDULERS = {"LV-Activity", "LV-Aging", "LV-AutoRestore"}
LEGACY_SCRIPTS = {"LV-Activity", "LV-Aging", "LV-AutoRestore"}
LEGACY_LOG_PREFIXES = {"LV-AUTH-PPP", "LV-AUTH-L2TP"}


def _disabled(row: dict[str, str]) -> bool:
    return str(row.get("disabled") or "").strip().lower() in {"true", "yes", "1", "on"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit or disable legacy desktop-owned LinkVideo RouterOS retention automation"
    )
    parser.add_argument(
        "--disable-legacy",
        action="store_true",
        help="disable legacy LV schedulers/logging after read-only audit; scripts are kept for rollback",
    )
    args = parser.parse_args()

    settings = get_settings()
    targets = load_targets(settings)
    if not targets:
        raise SystemExit("[RETENTION-MIGRATION] No RouterOS targets are configured")

    db = VPNDatabase(settings.database_url, settings.encryption_key)
    db.open()
    failures = 0
    changed = 0
    try:
        print(
            f"[RETENTION-MIGRATION] mode={'DISABLE' if args.disable_legacy else 'READ-ONLY'} targets={len(targets)}"
        )
        for target in targets:
            try:
                with RouterOSClient(
                    target.host,
                    target.username,
                    target.password,
                    port=target.port,
                    timeout=target.timeout,
                ) as api:
                    schedulers = api.print("/system/scheduler")
                    scripts = api.print("/system/script")
                    logging_rows = api.print("/system/logging")

                    legacy_sched = [row for row in schedulers if str(row.get("name") or "") in LEGACY_SCHEDULERS]
                    legacy_scripts = [row for row in scripts if str(row.get("name") or "") in LEGACY_SCRIPTS]
                    legacy_logging = [row for row in logging_rows if str(row.get("prefix") or "") in LEGACY_LOG_PREFIXES]
                    enabled_sched = [row for row in legacy_sched if not _disabled(row)]
                    enabled_logging = [row for row in legacy_logging if not _disabled(row)]

                    print(
                        f"[RETENTION-MIGRATION] {target.host}: scripts={len(legacy_scripts)} "
                        f"schedulers={len(legacy_sched)} enabled_schedulers={len(enabled_sched)} "
                        f"logging={len(legacy_logging)} enabled_logging={len(enabled_logging)}"
                    )

                    disabled_items: list[str] = []
                    if args.disable_legacy:
                        for row in enabled_sched:
                            item_id = str(row.get(".id") or "")
                            name = str(row.get("name") or "")
                            if item_id:
                                api.disable("/system/scheduler", item_id)
                                disabled_items.append(f"scheduler:{name}")
                        for row in enabled_logging:
                            item_id = str(row.get(".id") or "")
                            prefix = str(row.get("prefix") or "")
                            if item_id:
                                api.disable("/system/logging", item_id)
                                disabled_items.append(f"logging:{prefix}")

                        verify_sched = [
                            row for row in api.print("/system/scheduler")
                            if str(row.get("name") or "") in LEGACY_SCHEDULERS
                        ]
                        verify_logging = [
                            row for row in api.print("/system/logging")
                            if str(row.get("prefix") or "") in LEGACY_LOG_PREFIXES
                        ]
                        still_running = [
                            str(row.get("name") or row.get("prefix") or "")
                            for row in [*verify_sched, *verify_logging]
                            if not _disabled(row)
                        ]
                        if still_running:
                            raise RuntimeError("legacy automation still enabled: " + ", ".join(still_running))

                        if disabled_items:
                            changed += 1
                        with db.connection() as conn, conn.cursor() as cur:
                            cur.execute("SELECT id FROM vpn_servers WHERE hostname=%s", (target.host,))
                            server_row = cur.fetchone()
                            server_id = int(server_row["id"]) if server_row else None
                            cur.execute(
                                """
                                INSERT INTO vpnsync_audit_log
                                    (actor, role, source, action, server_id, success, details)
                                VALUES ('VPNSync migration', 'system', 'migration',
                                        'retention.legacy_disabled', %s, TRUE, %s::jsonb)
                                """,
                                (
                                    server_id,
                                    json.dumps(
                                        {
                                            "host": target.host,
                                            "disabled": disabled_items,
                                            "scripts_kept_for_rollback": [
                                                str(row.get("name") or "") for row in legacy_scripts
                                            ],
                                        },
                                        ensure_ascii=False,
                                        separators=(",", ":"),
                                    ),
                                ),
                            )
                            conn.commit()
                        print(f"[RETENTION-MIGRATION] {target.host}: legacy runtime DISABLED; scripts kept")
            except Exception as exc:
                failures += 1
                print(f"[RETENTION-MIGRATION] FAILED {target.host}: {exc}")

        print(
            f"[RETENTION-MIGRATION] COMPLETE mode={'DISABLE' if args.disable_legacy else 'READ-ONLY'} "
            f"changed_hosts={changed} failures={failures}"
        )
        if not args.disable_legacy:
            print("[RETENTION-MIGRATION] No RouterOS write command was executed.")
        if failures:
            raise SystemExit(2)
    finally:
        db.close()


if __name__ == "__main__":
    main()
