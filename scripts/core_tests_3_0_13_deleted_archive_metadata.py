from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    compat_path = "server/linkvideo_vpnsync/db_deleted_read_compat.py"
    init_path = "server/linkvideo_vpnsync/__init__.py"
    migration_path = "server/sql/004_routeros_address_values.sql"
    compat = read(compat_path)
    package_init = read(init_path)
    migration = read(migration_path)
    ast.parse(compat, filename=compat_path)
    ast.parse(package_init, filename=init_path)

    assert "install_deleted_read_compat" in package_init
    assert "ALTER COLUMN remote_address TYPE TEXT" in migration
    assert "ALTER COLUMN local_address TYPE TEXT" in migration
    assert "host(d.remote_address)" not in compat
    assert "host(d.local_address)" not in compat
    assert "COALESCE(d.remote_address, '') AS remote_address" in compat
    assert "COALESCE(d.local_address, '') AS local_address" in compat
    assert "strpos(lower(d.login), lower(%s)) > 0" in compat
    assert 'f"%{wanted.lower()}%"' not in compat
    assert "d.profile, d.service, d.routeros_comment" in compat
    assert "d.deleted_reason" in compat
    assert "d.deleted_by, d.source" in compat
    assert "(d.password_enc IS NOT NULL) AS password_saved" in compat
    assert "(d.recovery_snapshot_enc IS NOT NULL) AS snapshot_saved" in compat
    assert "CASE WHEN lower(d.login) = lower(%s) THEN 0 ELSE 1 END" in compat

    print("DELETED_ARCHIVE_METADATA_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
