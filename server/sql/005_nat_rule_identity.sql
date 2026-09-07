BEGIN;

-- RouterOS permits multiple dst-nat rules to share the same protocol/port when
-- other match fields differ. External port therefore cannot identify a NAT
-- rule. The stable identity available from RouterOS is the per-server .id.
--
-- Keep only one active row for any accidental duplicate .id before creating
-- the new partial unique index. Historical/deleted rows are intentionally kept.
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY server_id, routeros_rule_id
               ORDER BY last_sync_at DESC, updated_at DESC, id DESC
           ) AS rn
      FROM vpn_nat_ports
     WHERE deleted = FALSE
       AND routeros_rule_id <> ''
)
UPDATE vpn_nat_ports p
   SET deleted = TRUE,
       updated_at = now()
  FROM ranked r
 WHERE p.id = r.id
   AND r.rn > 1;

DROP INDEX IF EXISTS uq_vpn_nat_ports_active;

CREATE UNIQUE INDEX IF NOT EXISTS uq_vpn_nat_ports_routeros_rule_active
    ON vpn_nat_ports (server_id, routeros_rule_id)
    WHERE deleted = FALSE AND routeros_rule_id <> '';

CREATE INDEX IF NOT EXISTS idx_vpn_nat_ports_port_lookup
    ON vpn_nat_ports (server_id, protocol, external_port)
    WHERE deleted = FALSE;

COMMIT;
