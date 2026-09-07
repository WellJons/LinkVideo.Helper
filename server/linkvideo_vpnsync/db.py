from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class VPNDatabase:
    """PostgreSQL repository for LinkVideo.VPNSync.

    Active and deleted VPN clients intentionally live in different tables.
    Recovery secrets are encrypted with pgcrypto and are never returned by the
    normal search/list endpoints.
    """

    def __init__(self, database_url: str, encryption_key: str) -> None:
        self.database_url = str(database_url or "").strip()
        self.encryption_key = str(encryption_key or "")
        if not self.database_url:
            raise ValueError("DATABASE_URL is empty")
        if len(self.encryption_key) < 24:
            raise ValueError("VPNSYNC_ENCRYPTION_KEY is too short")
        self.pool = ConnectionPool(
            conninfo=self.database_url,
            min_size=1,
            max_size=10,
            timeout=10,
            kwargs={"row_factory": dict_row},
            open=False,
        )

    def open(self) -> None:
        self.pool.open(wait=True)

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self.pool.connection() as conn:
            yield conn

    def ping(self) -> dict[str, Any]:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT now() AS now, current_database() AS database, version() AS version")
            row = cur.fetchone() or {}
            return dict(row)

    def ensure_server(self, hostname: str, country: str = "") -> int:
        host = str(hostname or "").strip().lower()
        if not host:
            raise ValueError("hostname is empty")
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vpn_servers (hostname, country, last_sync_at)
                VALUES (%s, %s, now())
                ON CONFLICT (hostname) DO UPDATE
                   SET country = CASE WHEN EXCLUDED.country <> '' THEN EXCLUDED.country ELSE vpn_servers.country END,
                       updated_at = now()
                RETURNING id
                """,
                (host, str(country or "")),
            )
            server_id = int(cur.fetchone()["id"])
            conn.commit()
            return server_id

    def list_servers(self) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, hostname, country, enabled, active_l2tp, cpu_percent,
                       ram_percent, lv_version, lv_running, quarantine_enabled,
                       last_event_at, last_sync_at
                  FROM vpn_servers
                 ORDER BY hostname
                """
            )
            return [dict(row) for row in cur.fetchall()]

    @staticmethod
    def _snapshot_text(recovery_snapshot: dict[str, Any] | str | None) -> str:
        if isinstance(recovery_snapshot, str):
            return recovery_snapshot
        if recovery_snapshot is None:
            return ""
        return json.dumps(recovery_snapshot, ensure_ascii=False, separators=(",", ":"))

    def upsert_client(
        self,
        *,
        server_id: int,
        login: str,
        remote_address: str = "",
        local_address: str = "",
        profile: str = "",
        service: str = "",
        disabled: bool = False,
        lifecycle_state: str = "unknown",
        last_seen_at=None,
        first_seen_at=None,
        next_action_at=None,
        next_action_type: str | None = None,
        password: str = "",
        recovery_snapshot: dict[str, Any] | str | None = None,
        routeros_comment: str = "",
    ) -> int:
        snapshot_text = self._snapshot_text(recovery_snapshot)
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vpn_clients (
                    server_id, login, remote_address, local_address, profile, service,
                    disabled, lifecycle_state, last_seen_at, first_seen_at,
                    next_action_at, next_action_type, password_enc,
                    recovery_snapshot_enc, routeros_comment, last_sync_at
                ) VALUES (
                    %s, %s, NULLIF(%s, '')::inet, NULLIF(%s, '')::inet, %s, %s,
                    %s, %s, %s, COALESCE(%s, now()), %s, %s,
                    CASE WHEN %s = '' THEN NULL ELSE pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256') END,
                    CASE WHEN %s = '' THEN NULL ELSE pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256') END,
                    %s, now()
                )
                ON CONFLICT (server_id, login) DO UPDATE SET
                    remote_address = EXCLUDED.remote_address,
                    local_address = EXCLUDED.local_address,
                    profile = EXCLUDED.profile,
                    service = EXCLUDED.service,
                    disabled = EXCLUDED.disabled,
                    lifecycle_state = EXCLUDED.lifecycle_state,
                    last_seen_at = COALESCE(EXCLUDED.last_seen_at, vpn_clients.last_seen_at),
                    first_seen_at = LEAST(vpn_clients.first_seen_at, EXCLUDED.first_seen_at),
                    next_action_at = EXCLUDED.next_action_at,
                    next_action_type = EXCLUDED.next_action_type,
                    password_enc = COALESCE(EXCLUDED.password_enc, vpn_clients.password_enc),
                    recovery_snapshot_enc = COALESCE(EXCLUDED.recovery_snapshot_enc, vpn_clients.recovery_snapshot_enc),
                    routeros_comment = EXCLUDED.routeros_comment,
                    last_sync_at = now(),
                    updated_at = now()
                RETURNING id
                """,
                (
                    int(server_id), str(login), str(remote_address or ""), str(local_address or ""),
                    str(profile or ""), str(service or ""), bool(disabled), str(lifecycle_state or "unknown"),
                    last_seen_at, first_seen_at, next_action_at, next_action_type,
                    str(password or ""), str(password or ""), self.encryption_key,
                    snapshot_text, snapshot_text, self.encryption_key,
                    str(routeros_comment or ""),
                ),
            )
            client_id = int(cur.fetchone()["id"])
            conn.commit()
            return client_id

    def upsert_deleted_client(
        self,
        *,
        server_id: int,
        login: str,
        remote_address: str = "",
        local_address: str = "",
        profile: str = "",
        service: str = "",
        lifecycle_state: str = "deleted",
        last_seen_at=None,
        first_seen_at=None,
        deleted_at=None,
        deleted_reason: str = "",
        deleted_by: str = "",
        source: str = "migration",
        password: str = "",
        recovery_snapshot: dict[str, Any] | str | None = None,
        routeros_comment: str = "",
        ports: list[dict[str, Any]] | None = None,
    ) -> int:
        snapshot_text = self._snapshot_text(recovery_snapshot)
        ports_json = json.dumps(ports or [], ensure_ascii=False, separators=(",", ":"))
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vpn_deleted_clients (
                    server_id, login, remote_address, local_address, profile, service,
                    lifecycle_state, last_seen_at, first_seen_at, deleted_at,
                    deleted_reason, deleted_by, source, password_enc,
                    recovery_snapshot_enc, routeros_comment, ports
                ) VALUES (
                    %s, %s, NULLIF(%s, '')::inet, NULLIF(%s, '')::inet, %s, %s,
                    %s, %s, %s, COALESCE(%s, now()), %s, %s, %s,
                    CASE WHEN %s = '' THEN NULL ELSE pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256') END,
                    CASE WHEN %s = '' THEN NULL ELSE pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256') END,
                    %s, %s::jsonb
                )
                ON CONFLICT (server_id, login) DO UPDATE SET
                    remote_address = EXCLUDED.remote_address,
                    local_address = EXCLUDED.local_address,
                    profile = EXCLUDED.profile,
                    service = EXCLUDED.service,
                    lifecycle_state = EXCLUDED.lifecycle_state,
                    last_seen_at = COALESCE(EXCLUDED.last_seen_at, vpn_deleted_clients.last_seen_at),
                    first_seen_at = COALESCE(vpn_deleted_clients.first_seen_at, EXCLUDED.first_seen_at),
                    deleted_at = EXCLUDED.deleted_at,
                    deleted_reason = EXCLUDED.deleted_reason,
                    deleted_by = EXCLUDED.deleted_by,
                    source = EXCLUDED.source,
                    password_enc = COALESCE(EXCLUDED.password_enc, vpn_deleted_clients.password_enc),
                    recovery_snapshot_enc = COALESCE(EXCLUDED.recovery_snapshot_enc, vpn_deleted_clients.recovery_snapshot_enc),
                    routeros_comment = EXCLUDED.routeros_comment,
                    ports = EXCLUDED.ports,
                    updated_at = now()
                RETURNING id
                """,
                (
                    int(server_id), str(login), str(remote_address or ""), str(local_address or ""),
                    str(profile or ""), str(service or ""), str(lifecycle_state or "deleted"),
                    last_seen_at, first_seen_at, deleted_at,
                    str(deleted_reason or ""), str(deleted_by or ""), str(source or "migration"),
                    str(password or ""), str(password or ""), self.encryption_key,
                    snapshot_text, snapshot_text, self.encryption_key,
                    str(routeros_comment or ""), ports_json,
                ),
            )
            deleted_id = int(cur.fetchone()["id"])
            conn.commit()
            return deleted_id

    def archive_client(
        self,
        server_id: int,
        login: str,
        *,
        deleted_reason: str,
        deleted_by: str = "",
        source: str = "retention",
    ) -> bool:
        """Atomically move one active client and its recovery state to archive."""
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.*,
                       COALESCE((
                           SELECT jsonb_agg(jsonb_build_object(
                               'external_port', p.external_port,
                               'internal_port', p.internal_port,
                               'protocol', p.protocol,
                               'to_address', host(p.to_address),
                               'disabled', p.disabled,
                               'routeros_rule_id', p.routeros_rule_id
                           ) ORDER BY p.external_port)
                           FROM vpn_nat_ports p
                           WHERE p.client_id = c.id AND p.deleted = FALSE
                       ), '[]'::jsonb) AS ports
                  FROM vpn_clients c
                 WHERE c.server_id = %s AND c.login = %s
                 FOR UPDATE
                """,
                (int(server_id), str(login)),
            )
            row = cur.fetchone()
            if not row:
                return False
            cur.execute(
                """
                INSERT INTO vpn_deleted_clients (
                    server_id, login, remote_address, local_address, profile, service,
                    lifecycle_state, last_seen_at, first_seen_at, deleted_at,
                    deleted_reason, deleted_by, source, password_enc,
                    recovery_snapshot_enc, routeros_comment, ports
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, now(),
                    %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                ON CONFLICT (server_id, login) DO UPDATE SET
                    remote_address = EXCLUDED.remote_address,
                    local_address = EXCLUDED.local_address,
                    profile = EXCLUDED.profile,
                    service = EXCLUDED.service,
                    lifecycle_state = EXCLUDED.lifecycle_state,
                    last_seen_at = EXCLUDED.last_seen_at,
                    first_seen_at = EXCLUDED.first_seen_at,
                    deleted_at = EXCLUDED.deleted_at,
                    deleted_reason = EXCLUDED.deleted_reason,
                    deleted_by = EXCLUDED.deleted_by,
                    source = EXCLUDED.source,
                    password_enc = COALESCE(EXCLUDED.password_enc, vpn_deleted_clients.password_enc),
                    recovery_snapshot_enc = COALESCE(EXCLUDED.recovery_snapshot_enc, vpn_deleted_clients.recovery_snapshot_enc),
                    routeros_comment = EXCLUDED.routeros_comment,
                    ports = EXCLUDED.ports,
                    updated_at = now()
                """,
                (
                    int(server_id), str(login), row["remote_address"], row["local_address"],
                    row["profile"], row["service"], row["lifecycle_state"],
                    row["last_seen_at"], row["first_seen_at"],
                    str(deleted_reason or ""), str(deleted_by or ""), str(source or "retention"),
                    row["password_enc"], row["recovery_snapshot_enc"], row["routeros_comment"],
                    json.dumps(row["ports"] or [], ensure_ascii=False, default=str),
                ),
            )
            cur.execute("DELETE FROM vpn_nat_ports WHERE client_id = %s", (int(row["id"]),))
            cur.execute("DELETE FROM vpn_clients WHERE id = %s", (int(row["id"]),))
            conn.commit()
            return True

    def replace_nat_ports(self, server_id: int, client_id: int, rules: list[dict[str, Any]]) -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE vpn_nat_ports SET deleted = TRUE, updated_at = now() WHERE server_id = %s AND client_id = %s AND deleted = FALSE",
                (int(server_id), int(client_id)),
            )
            for rule in rules:
                external = int(rule.get("external_port") or 0)
                if not 1 <= external <= 65535:
                    continue
                internal = int(rule.get("internal_port") or external)
                protocol = str(rule.get("protocol") or "tcp").lower()
                cur.execute(
                    """
                    INSERT INTO vpn_nat_ports (
                        server_id, client_id, external_port, internal_port, protocol,
                        to_address, disabled, deleted, routeros_rule_id, last_sync_at
                    ) VALUES (%s, %s, %s, %s, %s, NULLIF(%s, '')::inet, %s, FALSE, %s, now())
                    ON CONFLICT (server_id, protocol, external_port) WHERE deleted = FALSE AND disabled = FALSE
                    DO UPDATE SET client_id = EXCLUDED.client_id,
                                  internal_port = EXCLUDED.internal_port,
                                  to_address = EXCLUDED.to_address,
                                  disabled = EXCLUDED.disabled,
                                  routeros_rule_id = EXCLUDED.routeros_rule_id,
                                  last_sync_at = now(), updated_at = now()
                    """,
                    (
                        int(server_id), int(client_id), external, internal, protocol,
                        str(rule.get("to_address") or ""), bool(rule.get("disabled", False)),
                        str(rule.get("routeros_rule_id") or ""),
                    ),
                )
            conn.commit()

    def search_clients(self, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
        wanted = str(query or "").strip()
        if not wanted:
            return []
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, s.hostname AS server, c.login,
                       host(c.remote_address) AS remote_address,
                       c.disabled, c.lifecycle_state, c.last_seen_at,
                       c.next_action_at, c.next_action_type,
                       FALSE AS deleted,
                       (c.password_enc IS NOT NULL) AS password_saved,
                       COALESCE((
                           SELECT jsonb_agg(jsonb_build_object(
                               'external_port', p.external_port,
                               'internal_port', p.internal_port,
                               'protocol', p.protocol,
                               'disabled', p.disabled
                           ) ORDER BY p.external_port)
                           FROM vpn_nat_ports p
                           WHERE p.client_id = c.id AND p.deleted = FALSE
                       ), '[]'::jsonb) AS ports
                  FROM vpn_clients c
                  JOIN vpn_servers s ON s.id = c.server_id
                 WHERE lower(c.login) LIKE %s
                 ORDER BY c.login, s.hostname
                 LIMIT %s
                """,
                (f"%{wanted.lower()}%", max(1, min(200, int(limit)))),
            )
            return [dict(row) for row in cur.fetchall()]

    def search_deleted_clients(self, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
        wanted = str(query or "").strip()
        if not wanted:
            return []
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id, s.hostname AS server, d.login,
                       host(d.remote_address) AS remote_address,
                       d.lifecycle_state, d.last_seen_at,
                       TRUE AS deleted, d.deleted_at, d.deleted_reason,
                       d.deleted_by, d.source,
                       (d.password_enc IS NOT NULL) AS password_saved,
                       d.ports
                  FROM vpn_deleted_clients d
                  JOIN vpn_servers s ON s.id = d.server_id
                 WHERE lower(d.login) LIKE %s
                 ORDER BY d.deleted_at DESC, d.login, s.hostname
                 LIMIT %s
                """,
                (f"%{wanted.lower()}%", max(1, min(200, int(limit)))),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_deleted_recovery(self, server: str, login: str) -> dict[str, Any] | None:
        """Return decrypted recovery material to trusted server-side restore code."""
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id, s.id AS server_id, s.hostname AS server, d.login,
                       host(d.remote_address) AS remote_address,
                       host(d.local_address) AS local_address,
                       d.profile, d.service, d.routeros_comment, d.ports,
                       CASE WHEN d.password_enc IS NULL THEN ''
                            ELSE pgp_sym_decrypt(d.password_enc, %s) END AS password,
                       CASE WHEN d.recovery_snapshot_enc IS NULL THEN ''
                            ELSE pgp_sym_decrypt(d.recovery_snapshot_enc, %s) END AS recovery_snapshot,
                       d.deleted_at, d.deleted_reason
                  FROM vpn_deleted_clients d
                  JOIN vpn_servers s ON s.id = d.server_id
                 WHERE lower(s.hostname) = lower(%s) AND d.login = %s
                """,
                (self.encryption_key, self.encryption_key, str(server or ""), str(login or "")),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def remove_deleted_client(self, server_id: int, login: str) -> bool:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM vpn_deleted_clients WHERE server_id = %s AND login = %s",
                (int(server_id), str(login)),
            )
            changed = cur.rowcount > 0
            conn.commit()
            return changed

    def append_change(
        self,
        *,
        server_id: int | None,
        client_id: int | None,
        login: str,
        event_type: str,
        summary: str = "",
        old_value: Any = None,
        new_value: Any = None,
        source: str = "sync",
        actor: str = "",
    ) -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vpn_change_log (
                    server_id, client_id, login, event_type, summary,
                    old_value, new_value, source, actor
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                """,
                (
                    server_id, client_id, str(login or ""), str(event_type or "changed"),
                    str(summary or ""),
                    json.dumps(old_value, ensure_ascii=False) if old_value is not None else None,
                    json.dumps(new_value, ensure_ascii=False) if new_value is not None else None,
                    str(source or "sync"), str(actor or ""),
                ),
            )
            conn.commit()
