#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter

from linkvideo_vpnsync.config import get_settings
from linkvideo_vpnsync.db import VPNDatabase
from linkvideo_vpnsync.monitor import load_targets
from linkvideo_vpnsync.routeros import RouterOSClient


def main() -> None:
    settings = get_settings()
    targets = load_targets(settings)
    if not targets:
        raise SystemExit("[AUDIT] RouterOS monitor is disabled or no targets are configured")

    db = VPNDatabase(settings.database_url, settings.encryption_key)
    db.open()
    try:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.hostname, c.login
                  FROM vpn_servers s
                  LEFT JOIN vpn_clients c ON c.server_id = s.id
                 WHERE s.hostname = ANY(%s)
                 ORDER BY s.hostname, c.login
                """,
                ([target.host for target in targets],),
            )
            db_by_host: dict[str, set[str]] = {target.host: set() for target in targets}
            for row in cur.fetchall():
                host = str(row["hostname"])
                login = row.get("login")
                if login is not None:
                    db_by_host.setdefault(host, set()).add(str(login))

        total_live = 0
        total_db = 0
        mismatch_hosts = 0

        print("[AUDIT] READ-ONLY RouterOS/PostgreSQL login-set comparison")
        for target in targets:
            with RouterOSClient(
                target.host,
                target.username,
                target.password,
                port=target.port,
                timeout=target.timeout,
            ) as api:
                secrets = api.print("/ppp/secret")
                connected_port = api.connected_port

            raw_names = [str(row.get("name") or "").strip() for row in secrets]
            named = [name for name in raw_names if name]
            counts = Counter(named)
            duplicate_names = sorted(name for name, count in counts.items() if count > 1)
            live = set(named)
            stored = db_by_host.get(target.host, set())
            missing = sorted(live - stored)
            extra = sorted(stored - live)
            blank = len(raw_names) - len(named)

            total_live += len(live)
            total_db += len(stored)
            ok = not missing and not extra and blank == 0 and not duplicate_names
            if not ok:
                mismatch_hosts += 1

            print(
                f"[AUDIT] {target.host}: port={connected_port} rows={len(secrets)} "
                f"live_unique={len(live)} db={len(stored)} blank={blank} "
                f"duplicates={len(duplicate_names)} missing_in_db={len(missing)} extra_in_db={len(extra)} "
                f"status={'OK' if ok else 'MISMATCH'}"
            )
            if duplicate_names:
                print(f"[AUDIT]   duplicate logins: {duplicate_names[:20]}")
            if missing:
                print(f"[AUDIT]   missing in DB: {missing[:20]}")
            if extra:
                print(f"[AUDIT]   extra in DB: {extra[:20]}")

        print(
            f"[AUDIT] COMPLETE targets={len(targets)} live_unique_total={total_live} "
            f"db_total={total_db} mismatch_hosts={mismatch_hosts}"
        )
        print("[AUDIT] No RouterOS or PostgreSQL write command was executed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
