# LinkVideo.VPNSync server

Server-side VPN state service for LinkVideo.Helper.

Target architecture:

RouterOS vpn01..vpnNN -> LinkVideo.VPNSync -> PostgreSQL -> LinkVideo.Helper / reports / optional Google Sheets export

The service is intended to run continuously on a Linux host. Desktop Helper must not need to stay open for event collection or retention scheduling.

## Security model

- PostgreSQL is local/private to the server and is not exposed to employee desktops.
- Helper talks to the VPNSync HTTP API using a bearer token.
- RouterOS credentials and the database encryption key are provided to the service through root-readable environment/config files, never committed to Git.
- PPP passwords and full recovery snapshots are encrypted before storage in PostgreSQL.
- Google Sheets remains an optional export/transition backend during migration.

## First deployment

Exact package commands depend on the Linux distribution. Before installation collect:

```bash
cat /etc/os-release
hostname -f 2>/dev/null || hostname
ip -br addr
nproc
free -h
df -h /
```

Do not commit or paste SSH, RouterOS, PostgreSQL, API, or encryption passwords into the repository.

## Environment

See `.env.example`. Production secrets should normally live in `/etc/linkvideo-vpnsync/vpnsync.env` with permissions `0600`.

## Database

Run `sql/001_init.sql` as the database owner. PostgreSQL extension `pgcrypto` is used to encrypt recovery secrets.
