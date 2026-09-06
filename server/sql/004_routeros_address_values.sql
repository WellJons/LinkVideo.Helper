BEGIN;

-- PPP profile local-address/remote-address are not guaranteed to be literal IPs.
-- RouterOS also accepts pool names such as "vpn-pool". Preserve the original
-- RouterOS value verbatim; recovery snapshots already keep the full object too.
ALTER TABLE vpn_clients
    ALTER COLUMN remote_address TYPE TEXT USING remote_address::text,
    ALTER COLUMN local_address TYPE TEXT USING local_address::text;

ALTER TABLE vpn_deleted_clients
    ALTER COLUMN remote_address TYPE TEXT USING remote_address::text,
    ALTER COLUMN local_address TYPE TEXT USING local_address::text;

COMMIT;
