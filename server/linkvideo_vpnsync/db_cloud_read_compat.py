from __future__ import annotations

import json
from typing import Any


def install_cloud_read_compat() -> None:
    """Add rich PostgreSQL client reads used by authenticated desktop search."""
    from . import db as db_module

    cls = db_module.VPNDatabase
    if getattr(cls, "_cloud_read_compat", False):
        return

    def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        snapshot_text = str(data.pop("recovery_snapshot", "") or "")
        try:
            snapshot = json.loads(snapshot_text) if snapshot_text else {}
        except Exception:
            snapshot = {}
        if not isinstance(snapshot, dict):
            snapshot = {}

        secret = snapshot.get("secret") if isinstance(snapshot.get("secret"), dict) else {}
        active = snapshot.get("active") if isinstance(snapshot.get("active"), dict) else {}
        port_rows = list(data.pop("port_rows", []) or [])
        ports: list[int] = []
        disabled_ports: list[int] = []
        nat_rule_ids: list[str] = []
        for item in port_rows:
            try:
                port = int(item.get("external_port") or 0)
            except Exception:
                continue
            if not 1 <= port <= 65535:
                continue
            if port not in ports:
                ports.append(port)
            if bool(item.get("disabled")) and port not in disabled_ports:
                disabled_ports.append(port)
            rule_id = str(item.get("routeros_rule_id") or "")
            if rule_id and rule_id not in nat_rule_ids:
                nat_rule_ids.append(rule_id)

        data["remote_address"] = str(data.get("remote_address") or "")
        data["local_address"] = str(data.get("local_address") or "")
        data["ports"] = sorted(ports)
        data["disabled_ports"] = sorted(disabled_ports)
        data["active_ports"] = []
        data["nat_rule_ids"] = nat_rule_ids
        data["is_enabled"] = not bool(data.pop("disabled", False))
        data["is_online"] = bool(active)
        data["last_logged_out"] = str(secret.get("last-logged-out") or secret.get("last_logged_out") or "")
        data["uptime"] = str(active.get("uptime") or "")
        data["port_conflicts"] = {}
        return data

    def _attach_conflicts(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ids = [int(row["id"]) for row in rows if row.get("id") is not None]
        if not ids:
            return rows
        by_id = {int(row["id"]): row for row in rows if row.get("id") is not None}
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.client_id, p.external_port,
                       COALESCE(c2.login, '') AS owner_login,
                       COALESCE(c2.remote_address, '') AS owner_remote_address,
                       COALESCE(p2.routeros_rule_id, '') AS routeros_rule_id,
                       p2.disabled
                  FROM vpn_nat_ports p
                  JOIN vpn_nat_ports p2
                    ON p2.server_id = p.server_id
                   AND p2.external_port = p.external_port
                   AND p2.deleted = FALSE
                   AND p2.client_id IS DISTINCT FROM p.client_id
                  LEFT JOIN vpn_clients c2 ON c2.id = p2.client_id
                 WHERE p.client_id = ANY(%s)
                   AND p.deleted = FALSE
                 ORDER BY p.client_id, p.external_port, c2.login
                """,
                (ids,),
            )
            conflicts = cur.fetchall()
        for item in conflicts:
            owner = by_id.get(int(item["client_id"]))
            if owner is None:
                continue
            port = int(item["external_port"])
            owner["port_conflicts"].setdefault(port, []).append({
                "port": port,
                "rule_id": str(item.get("routeros_rule_id") or ""),
                "owner_login": str(item.get("owner_login") or ""),
                "owner_remote_address": str(item.get("owner_remote_address") or ""),
                "owner_comment": "",
                "disabled": bool(item.get("disabled")),
            })
        return rows

    def search_client_details(
        self,
        query: str,
        *,
        limit: int = 50,
        include_password: bool = False,
        servers: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        wanted = str(query or "").strip()
        if not wanted:
            return []
        maximum = max(1, min(200, int(limit)))
        port = int(wanted) if wanted.isdigit() and 1 <= int(wanted) <= 65535 else None
        server_names = [str(item or "").strip().lower() for item in list(servers or []) if str(item or "").strip()]
        server_clause = "AND lower(s.hostname) = ANY(%s)" if server_names else ""
        params: list[Any] = [
            bool(include_password), self.encryption_key,
            self.encryption_key,
            f"%{wanted.lower()}%",
            f"%{wanted.lower()}%",
            port,
            port,
        ]
        if server_names:
            params.append(server_names)
        params.append(maximum)

        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT c.id, s.hostname AS server, c.login,
                       COALESCE(c.remote_address, '') AS remote_address,
                       COALESCE(c.local_address, '') AS local_address,
                       c.profile, c.service, c.disabled, c.lifecycle_state,
                       c.last_seen_at, c.first_seen_at, c.next_action_at,
                       c.next_action_type, c.routeros_comment,
                       CASE WHEN %s AND c.password_enc IS NOT NULL
                            THEN pgp_sym_decrypt(c.password_enc, %s)
                            ELSE '' END AS password,
                       CASE WHEN c.recovery_snapshot_enc IS NOT NULL
                            THEN pgp_sym_decrypt(c.recovery_snapshot_enc, %s)
                            ELSE '' END AS recovery_snapshot,
                       COALESCE((
                           SELECT jsonb_agg(jsonb_build_object(
                               'external_port', p.external_port,
                               'internal_port', p.internal_port,
                               'protocol', p.protocol,
                               'disabled', p.disabled,
                               'routeros_rule_id', p.routeros_rule_id
                           ) ORDER BY p.external_port, p.routeros_rule_id)
                             FROM vpn_nat_ports p
                            WHERE p.client_id = c.id AND p.deleted = FALSE
                       ), '[]'::jsonb) AS port_rows
                  FROM vpn_clients c
                  JOIN vpn_servers s ON s.id = c.server_id
                 WHERE (
                       lower(c.login) LIKE %s
                    OR lower(COALESCE(c.remote_address, '')) LIKE %s
                    OR (%s IS NOT NULL AND EXISTS (
                           SELECT 1 FROM vpn_nat_ports px
                            WHERE px.client_id=c.id AND px.deleted=FALSE
                              AND px.external_port=%s
                       ))
                 )
                 {server_clause}
                 ORDER BY
                    CASE WHEN lower(c.login)=lower(%s) THEN 0 ELSE 1 END,
                    c.login, s.hostname
                 LIMIT %s
                """,
                tuple(params[:-1] + [wanted, params[-1]]),
            )
            raw = [dict(row) for row in cur.fetchall()]
        decoded = [_decode_row(row) for row in raw]
        return _attach_conflicts(self, decoded)

    def get_client_detail(
        self,
        server: str,
        login: str,
        *,
        include_password: bool = False,
    ) -> dict[str, Any] | None:
        wanted_server = str(server or "").strip().lower()
        wanted_login = str(login or "").strip()
        if not wanted_server or not wanted_login:
            return None
        rows = search_client_details(
            self,
            wanted_login,
            limit=50,
            include_password=include_password,
            servers=[wanted_server],
        )
        return next(
            (
                row for row in rows
                if str(row.get("server") or "").lower() == wanted_server
                and str(row.get("login") or "") == wanted_login
            ),
            None,
        )

    cls.search_client_details = search_client_details
    cls.get_client_detail = get_client_detail
    cls._cloud_read_compat = True
