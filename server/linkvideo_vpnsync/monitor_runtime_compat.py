from __future__ import annotations

from typing import Any


def install_monitor_runtime_compat() -> None:
    """Compatibility/runtime additions for the central RouterOS monitor.

    Migration 004 changed PPP local/remote address columns from INET to TEXT
    because RouterOS profiles can reference pool names such as ``vpn-pool``.
    SnapshotSync must therefore stop calling PostgreSQL host() on these fields.

    The NAT projection deliberately matches ``SnapshotSync._ports()`` exactly.
    Without ``to_address`` and ``routeros_rule_id`` on the database side every
    client with NAT rules looked changed after the Sheets -> PostgreSQL import,
    even when RouterOS had not changed at all.

    The status helper intentionally exposes no credentials. It is used by the
    health endpoint during the staged RouterOS listener rollout.
    """

    from . import monitor as monitor_module

    sync_cls = monitor_module.SnapshotSync
    manager_cls = monitor_module.RouterOSMonitorManager
    if getattr(sync_cls, "_runtime_compat", False):
        return

    def existing(self, server_id: int) -> dict[str, dict[str, Any]]:
        with self.db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.login, c.lifecycle_state, c.disabled,
                       c.first_seen_at, c.last_seen_at,
                       c.remote_address AS remote_address,
                       c.local_address AS local_address,
                       c.profile, c.service, c.routeros_comment,
                       COALESCE((
                           SELECT jsonb_agg(jsonb_build_object(
                               'external_port', p.external_port,
                               'internal_port', p.internal_port,
                               'protocol', p.protocol,
                               'to_address', COALESCE(host(p.to_address), ''),
                               'disabled', p.disabled,
                               'routeros_rule_id', p.routeros_rule_id
                           ) ORDER BY p.external_port, p.protocol)
                           FROM vpn_nat_ports p
                           WHERE p.client_id = c.id AND p.deleted = FALSE
                       ), '[]'::jsonb) AS ports
                  FROM vpn_clients c
                 WHERE c.server_id = %s
                """,
                (int(server_id),),
            )
            return {str(row["login"]): dict(row) for row in cur.fetchall()}

    def status(self) -> dict[str, Any]:
        target_hosts = [target.host for target in self.targets]
        listener_by_host = {listener.target.host: listener for listener in self.listeners}
        db_rows: dict[str, dict[str, Any]] = {}
        if target_hosts:
            with self.db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT hostname, active_l2tp, last_sync_at, last_event_at
                      FROM vpn_servers
                     WHERE hostname = ANY(%s)
                    """,
                    (target_hosts,),
                )
                db_rows = {str(row["hostname"]): dict(row) for row in cur.fetchall()}

        servers: list[dict[str, Any]] = []
        workers_alive = 0
        for target in self.targets:
            listener = listener_by_host.get(target.host)
            thread = getattr(listener, "_thread", None) if listener is not None else None
            worker_alive = bool(thread and thread.is_alive())
            if worker_alive:
                workers_alive += 1
            db_row = db_rows.get(target.host, {})
            servers.append({
                "host": target.host,
                "worker_alive": worker_alive,
                "active_l2tp": int(db_row.get("active_l2tp") or 0),
                "last_sync_at": db_row.get("last_sync_at"),
                "last_event_at": db_row.get("last_event_at"),
            })

        return {
            "enabled": bool(self.targets),
            "target_count": len(self.targets),
            "workers_alive": workers_alive,
            "servers": servers,
        }

    sync_cls._existing = existing
    manager_cls.status = status
    sync_cls._runtime_compat = True
