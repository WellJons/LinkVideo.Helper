BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS vpn_servers (
    id BIGSERIAL PRIMARY KEY,
    hostname TEXT NOT NULL UNIQUE,
    country TEXT NOT NULL DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    active_l2tp INTEGER NOT NULL DEFAULT 0 CHECK (active_l2tp >= 0),
    cpu_percent NUMERIC(5,2),
    ram_percent NUMERIC(5,2),
    lv_version TEXT NOT NULL DEFAULT '',
    lv_running BOOLEAN NOT NULL DEFAULT FALSE,
    quarantine_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    last_event_at TIMESTAMPTZ,
    last_sync_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vpn_clients (
    id BIGSERIAL PRIMARY KEY,
    server_id BIGINT NOT NULL REFERENCES vpn_servers(id) ON DELETE CASCADE,
    login TEXT NOT NULL,
    remote_address INET,
    local_address INET,
    profile TEXT NOT NULL DEFAULT '',
    service TEXT NOT NULL DEFAULT '',
    disabled BOOLEAN NOT NULL DEFAULT FALSE,
    lifecycle_state TEXT NOT NULL DEFAULT 'unknown',
    last_seen_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    next_action_at TIMESTAMPTZ,
    next_action_type TEXT,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    deleted_reason TEXT NOT NULL DEFAULT '',
    password_enc BYTEA,
    recovery_snapshot_enc BYTEA,
    routeros_comment TEXT NOT NULL DEFAULT '',
    last_sync_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (server_id, login)
);

CREATE INDEX IF NOT EXISTS idx_vpn_clients_login ON vpn_clients (lower(login));
CREATE INDEX IF NOT EXISTS idx_vpn_clients_state ON vpn_clients (deleted, lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_vpn_clients_next_action ON vpn_clients (next_action_at)
    WHERE deleted = FALSE AND next_action_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS vpn_nat_ports (
    id BIGSERIAL PRIMARY KEY,
    server_id BIGINT NOT NULL REFERENCES vpn_servers(id) ON DELETE CASCADE,
    client_id BIGINT REFERENCES vpn_clients(id) ON DELETE SET NULL,
    external_port INTEGER NOT NULL CHECK (external_port BETWEEN 1 AND 65535),
    internal_port INTEGER CHECK (internal_port BETWEEN 1 AND 65535),
    protocol TEXT NOT NULL DEFAULT 'tcp',
    to_address INET,
    disabled BOOLEAN NOT NULL DEFAULT FALSE,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    routeros_rule_id TEXT NOT NULL DEFAULT '',
    last_sync_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_vpn_nat_ports_active
    ON vpn_nat_ports (server_id, protocol, external_port)
    WHERE deleted = FALSE AND disabled = FALSE;
CREATE INDEX IF NOT EXISTS idx_vpn_nat_ports_client ON vpn_nat_ports (client_id);

CREATE TABLE IF NOT EXISTS vpn_change_log (
    id BIGSERIAL PRIMARY KEY,
    operation_id UUID NOT NULL DEFAULT gen_random_uuid(),
    server_id BIGINT REFERENCES vpn_servers(id) ON DELETE SET NULL,
    client_id BIGINT REFERENCES vpn_clients(id) ON DELETE SET NULL,
    login TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    old_value JSONB,
    new_value JSONB,
    source TEXT NOT NULL DEFAULT 'sync',
    actor TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_vpn_change_log_created ON vpn_change_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vpn_change_log_login ON vpn_change_log (lower(login), created_at DESC);

CREATE TABLE IF NOT EXISTS vpn_backups (
    id BIGSERIAL PRIMARY KEY,
    server_id BIGINT NOT NULL REFERENCES vpn_servers(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sha256 TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    changed_since_previous BOOLEAN NOT NULL DEFAULT TRUE,
    status TEXT NOT NULL DEFAULT 'created',
    error_text TEXT NOT NULL DEFAULT '',
    UNIQUE (server_id, sha256)
);

CREATE TABLE IF NOT EXISTS sync_events (
    id BIGSERIAL PRIMARY KEY,
    server_id BIGINT REFERENCES vpn_servers(id) ON DELETE CASCADE,
    routeros_path TEXT NOT NULL,
    routeros_item_id TEXT NOT NULL DEFAULT '',
    event_kind TEXT NOT NULL DEFAULT 'changed',
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ,
    payload JSONB
);
CREATE INDEX IF NOT EXISTS idx_sync_events_pending ON sync_events (processed_at, observed_at)
    WHERE processed_at IS NULL;

COMMIT;
