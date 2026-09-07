BEGIN;

CREATE TABLE IF NOT EXISTS vpn_deleted_clients (
    id BIGSERIAL PRIMARY KEY,
    server_id BIGINT NOT NULL REFERENCES vpn_servers(id) ON DELETE CASCADE,
    login TEXT NOT NULL,
    remote_address INET,
    local_address INET,
    profile TEXT NOT NULL DEFAULT '',
    service TEXT NOT NULL DEFAULT '',
    lifecycle_state TEXT NOT NULL DEFAULT 'deleted',
    last_seen_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_reason TEXT NOT NULL DEFAULT '',
    deleted_by TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'migration',
    password_enc BYTEA,
    recovery_snapshot_enc BYTEA,
    routeros_comment TEXT NOT NULL DEFAULT '',
    ports JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (server_id, login)
);

CREATE INDEX IF NOT EXISTS idx_vpn_deleted_clients_login
    ON vpn_deleted_clients (lower(login));
CREATE INDEX IF NOT EXISTS idx_vpn_deleted_clients_deleted_at
    ON vpn_deleted_clients (deleted_at DESC);

-- Compatibility migration for databases that already received 001_init.sql.
-- Capture the final known NAT state before active rows are removed.
INSERT INTO vpn_deleted_clients (
    server_id, login, remote_address, local_address, profile, service,
    lifecycle_state, last_seen_at, first_seen_at, deleted_at, deleted_reason,
    source, password_enc, recovery_snapshot_enc, routeros_comment, ports
)
SELECT
    c.server_id,
    c.login,
    c.remote_address,
    c.local_address,
    c.profile,
    c.service,
    c.lifecycle_state,
    c.last_seen_at,
    c.first_seen_at,
    COALESCE(c.deleted_at, now()),
    c.deleted_reason,
    'schema-migration',
    c.password_enc,
    c.recovery_snapshot_enc,
    c.routeros_comment,
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
    ), '[]'::jsonb)
FROM vpn_clients c
WHERE c.deleted = TRUE
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
    password_enc = COALESCE(EXCLUDED.password_enc, vpn_deleted_clients.password_enc),
    recovery_snapshot_enc = COALESCE(EXCLUDED.recovery_snapshot_enc, vpn_deleted_clients.recovery_snapshot_enc),
    routeros_comment = EXCLUDED.routeros_comment,
    ports = EXCLUDED.ports,
    updated_at = now();

DELETE FROM vpn_nat_ports
 WHERE client_id IN (SELECT id FROM vpn_clients WHERE deleted = TRUE);
DELETE FROM vpn_clients WHERE deleted = TRUE;

DROP INDEX IF EXISTS idx_vpn_clients_state;
ALTER TABLE vpn_clients DROP COLUMN IF EXISTS deleted;
ALTER TABLE vpn_clients DROP COLUMN IF EXISTS deleted_at;
ALTER TABLE vpn_clients DROP COLUMN IF EXISTS deleted_reason;
CREATE INDEX IF NOT EXISTS idx_vpn_clients_state ON vpn_clients (lifecycle_state);

COMMIT;
