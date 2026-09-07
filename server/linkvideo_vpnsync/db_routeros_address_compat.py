from __future__ import annotations

import json
from typing import Any


def install_routeros_address_compat() -> None:
    """Patch VPNDatabase for RouterOS PPP address values stored as TEXT.

    RouterOS PPP profile local-address/remote-address may contain pool names
    (for example ``vpn-pool``), not only literal IP addresses. PostgreSQL INET
    therefore is too strict for these two fields. Migration 004 converts them
    to TEXT and this compatibility layer removes the old ::inet/host() casts.
    """

    from . import db as db_module

    cls = db_module.VPNDatabase
    if getattr(cls, "_routeros_address_compat", False):
        return

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
                    %s, %s, NULLIF(%s, ''), NULLIF(%s, ''), %s, %s,
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
                    %s, %s, NULLIF(%s, ''), NULLIF(%s, ''), %s, %s,
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

    def search_clients(self, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
        wanted = str(query or "").strip()
        if not wanted:
            return []
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, s.hostname AS server, c.login,
                       c.remote_address AS remote_address,
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
                       d.remote_address AS remote_address,
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
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id, s.id AS server_id, s.hostname AS server, d.login,
                       d.remote_address AS remote_address,
                       d.local_address AS local_address,
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

    cls.upsert_client = upsert_client
    cls.upsert_deleted_client = upsert_deleted_client
    cls.search_clients = search_clients
    cls.search_deleted_clients = search_deleted_clients
    cls.get_deleted_recovery = get_deleted_recovery
    cls._routeros_address_compat = True
