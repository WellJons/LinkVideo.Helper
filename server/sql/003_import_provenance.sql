BEGIN;

ALTER TABLE vpn_change_log
    ADD COLUMN IF NOT EXISTS external_event_id TEXT NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS uq_vpn_change_log_external_event
    ON vpn_change_log (external_event_id)
    WHERE external_event_id <> '';

CREATE TABLE IF NOT EXISTS vpnsync_import_runs (
    source TEXT PRIMARY KEY,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

COMMIT;
