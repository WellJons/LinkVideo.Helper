from __future__ import annotations

from typing import Any


def install_nat_rule_compat() -> None:
    """Patch NAT persistence to use RouterOS rule identity, not external port.

    RouterOS may legitimately contain multiple dst-nat rules with the same
    protocol/external port when other match fields differ. A rule's per-server
    ``.id`` is therefore the stable identity for live synchronization.

    Rows imported from legacy data may not have a RouterOS rule id. Those rows
    use an exact-value/client fallback so repeated imports remain idempotent.
    """

    from . import db as db_module

    cls = db_module.VPNDatabase
    if getattr(cls, "_nat_rule_compat", False):
        return

    def replace_nat_ports(self, server_id: int, client_id: int, rules: list[dict[str, Any]]) -> None:
        server_id = int(server_id)
        client_id = int(client_id)

        normalized: list[dict[str, Any]] = []
        desired_rule_ids: list[str] = []
        for rule in rules:
            external = int(rule.get("external_port") or 0)
            if not 1 <= external <= 65535:
                continue
            internal = int(rule.get("internal_port") or external)
            if not 1 <= internal <= 65535:
                internal = external
            protocol = str(rule.get("protocol") or "tcp").strip().lower() or "tcp"
            routeros_rule_id = str(rule.get("routeros_rule_id") or "").strip()
            item = {
                "external_port": external,
                "internal_port": internal,
                "protocol": protocol,
                "to_address": str(rule.get("to_address") or "").strip(),
                "disabled": bool(rule.get("disabled", False)),
                "routeros_rule_id": routeros_rule_id,
            }
            normalized.append(item)
            if routeros_rule_id:
                desired_rule_ids.append(routeros_rule_id)

        with self.connection() as conn, conn.cursor() as cur:
            # Retire live rules with RouterOS ids that no longer belong to this
            # client. Rules still present are left active so ON CONFLICT can
            # update them in place instead of creating a historical row every
            # synchronization.
            cur.execute(
                """
                UPDATE vpn_nat_ports
                   SET deleted = TRUE,
                       updated_at = now()
                 WHERE server_id = %s
                   AND client_id = %s
                   AND deleted = FALSE
                   AND routeros_rule_id <> ''
                   AND NOT (routeros_rule_id = ANY(%s::text[]))
                """,
                (server_id, client_id, desired_rule_ids),
            )

            # Legacy/imported rows without a RouterOS .id are reconciled by
            # exact values below. Mark them stale first, then revive a matching
            # row or insert one if none exists.
            cur.execute(
                """
                UPDATE vpn_nat_ports
                   SET deleted = TRUE,
                       updated_at = now()
                 WHERE server_id = %s
                   AND client_id = %s
                   AND deleted = FALSE
                   AND routeros_rule_id = ''
                """,
                (server_id, client_id),
            )

            for item in normalized:
                external = item["external_port"]
                internal = item["internal_port"]
                protocol = item["protocol"]
                to_address = item["to_address"]
                disabled = item["disabled"]
                routeros_rule_id = item["routeros_rule_id"]

                if routeros_rule_id:
                    cur.execute(
                        """
                        INSERT INTO vpn_nat_ports (
                            server_id, client_id, external_port, internal_port,
                            protocol, to_address, disabled, deleted,
                            routeros_rule_id, last_sync_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, NULLIF(%s, '')::inet,
                            %s, FALSE, %s, now()
                        )
                        ON CONFLICT (server_id, routeros_rule_id)
                            WHERE deleted = FALSE AND routeros_rule_id <> ''
                        DO UPDATE SET
                            client_id = EXCLUDED.client_id,
                            external_port = EXCLUDED.external_port,
                            internal_port = EXCLUDED.internal_port,
                            protocol = EXCLUDED.protocol,
                            to_address = EXCLUDED.to_address,
                            disabled = EXCLUDED.disabled,
                            deleted = FALSE,
                            last_sync_at = now(),
                            updated_at = now()
                        """,
                        (
                            server_id, client_id, external, internal, protocol,
                            to_address, disabled, routeros_rule_id,
                        ),
                    )
                    continue

                # No stable RouterOS id (legacy Sheets import). Reuse the most
                # recent exact row to keep repeated imports/syncs idempotent.
                cur.execute(
                    """
                    SELECT id
                      FROM vpn_nat_ports
                     WHERE server_id = %s
                       AND client_id = %s
                       AND routeros_rule_id = ''
                       AND protocol = %s
                       AND external_port = %s
                       AND COALESCE(internal_port, external_port) = %s
                       AND COALESCE(host(to_address), '') = %s
                       AND disabled = %s
                     ORDER BY id DESC
                     LIMIT 1
                    """,
                    (
                        server_id, client_id, protocol, external, internal,
                        to_address, disabled,
                    ),
                )
                row = cur.fetchone()
                if row:
                    cur.execute(
                        """
                        UPDATE vpn_nat_ports
                           SET deleted = FALSE,
                               last_sync_at = now(),
                               updated_at = now()
                         WHERE id = %s
                        """,
                        (int(row["id"]),),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO vpn_nat_ports (
                            server_id, client_id, external_port, internal_port,
                            protocol, to_address, disabled, deleted,
                            routeros_rule_id, last_sync_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, NULLIF(%s, '')::inet,
                            %s, FALSE, '', now()
                        )
                        """,
                        (
                            server_id, client_id, external, internal, protocol,
                            to_address, disabled,
                        ),
                    )

            conn.commit()

    cls.replace_nat_ports = replace_nat_ports
    cls._nat_rule_compat = True
