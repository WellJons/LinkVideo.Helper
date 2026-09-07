BEGIN;

CREATE TABLE IF NOT EXISTS vpnsync_users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'operator',
    password_salt BYTEA NOT NULL,
    password_hash BYTEA NOT NULL,
    password_iterations INTEGER NOT NULL DEFAULT 390000,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_vpnsync_users_username
    ON vpnsync_users (lower(username));

CREATE TABLE IF NOT EXISTS vpnsync_audit_log (
    id BIGSERIAL PRIMARY KEY,
    operation_id UUID NOT NULL DEFAULT gen_random_uuid(),
    actor TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'server',
    action TEXT NOT NULL,
    server_id BIGINT REFERENCES vpn_servers(id) ON DELETE SET NULL,
    login TEXT NOT NULL DEFAULT '',
    success BOOLEAN NOT NULL DEFAULT TRUE,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_vpnsync_audit_log_created
    ON vpnsync_audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vpnsync_audit_log_actor
    ON vpnsync_audit_log (lower(actor), created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vpnsync_audit_log_login
    ON vpnsync_audit_log (lower(login), created_at DESC)
    WHERE login <> '';

CREATE OR REPLACE VIEW vpnsync_activity AS
SELECT
    ('change:' || l.id::text) AS activity_id,
    l.operation_id,
    l.created_at,
    l.source,
    l.actor,
    ''::text AS role,
    l.event_type AS action,
    l.server_id,
    l.login,
    TRUE AS success,
    jsonb_build_object(
        'summary', l.summary,
        'old_value', l.old_value,
        'new_value', l.new_value,
        'kind', 'change'
    ) AS details
FROM vpn_change_log l
UNION ALL
SELECT
    ('audit:' || a.id::text) AS activity_id,
    a.operation_id,
    a.created_at,
    a.source,
    a.actor,
    a.role,
    a.action,
    a.server_id,
    a.login,
    a.success,
    a.details || jsonb_build_object('kind', 'audit') AS details
FROM vpnsync_audit_log a
UNION ALL
SELECT
    ('routeros:' || e.id::text) AS activity_id,
    NULL::uuid AS operation_id,
    e.observed_at AS created_at,
    'RouterOS listen'::text AS source,
    'RouterOS'::text AS actor,
    ''::text AS role,
    ('routeros.' || COALESCE(NULLIF(e.event_kind, ''), 'changed'))::text AS action,
    e.server_id,
    COALESCE(e.payload->>'name', '') AS login,
    TRUE AS success,
    jsonb_build_object(
        'kind', 'routeros_event',
        'path', e.routeros_path,
        'item_id', e.routeros_item_id,
        'payload', COALESCE(e.payload, '{}'::jsonb),
        'processed_at', e.processed_at
    ) AS details
FROM sync_events e;

COMMIT;
