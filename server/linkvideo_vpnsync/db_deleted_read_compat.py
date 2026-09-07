from __future__ import annotations

from typing import Any


def install_deleted_read_compat() -> None:
    """Expose complete deleted-client metadata to the authenticated recovery UI.

    PostgreSQL remains an archive/backup source only. This read adapter adds the
    fields needed to show a trustworthy recovery card after the live MikroTik
    search has already completed.
    """
    from . import db as db_module

    cls = db_module.VPNDatabase
    if getattr(cls, "_deleted_read_compat", False):
        return

    def search_deleted_clients(self, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
        wanted = str(query or "").strip()
        if not wanted:
            return []
        maximum = max(1, min(200, int(limit)))
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id, s.hostname AS server, d.login,
                       host(d.remote_address) AS remote_address,
                       host(d.local_address) AS local_address,
                       d.profile, d.service, d.routeros_comment,
                       d.lifecycle_state, d.last_seen_at, d.first_seen_at,
                       TRUE AS deleted, d.deleted_at, d.deleted_reason,
                       d.deleted_by, d.source,
                       (d.password_enc IS NOT NULL) AS password_saved,
                       (d.recovery_snapshot_enc IS NOT NULL) AS snapshot_saved,
                       d.ports
                  FROM vpn_deleted_clients d
                  JOIN vpn_servers s ON s.id = d.server_id
                 WHERE lower(d.login) LIKE %s
                 ORDER BY
                       CASE WHEN lower(d.login) = lower(%s) THEN 0 ELSE 1 END,
                       d.deleted_at DESC, d.login, s.hostname
                 LIMIT %s
                """,
                (f"%{wanted.lower()}%", wanted, maximum),
            )
            return [dict(row) for row in cur.fetchall()]

    cls.search_deleted_clients = search_deleted_clients
    cls._deleted_read_compat = True
