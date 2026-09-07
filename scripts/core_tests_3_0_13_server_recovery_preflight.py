from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    source = (ROOT / "server/install_ubuntu.sh").read_text(encoding="utf-8")

    for route in (
        "/v1/deleted/search",
        "/v1/archive/restore/preflight",
        "/v1/archive/restore/server-preflight",
        "/v1/archive/restore",
    ):
        assert route in source

    # PostgreSQL is passive backup/archive storage. Installer preflight must
    # validate deleted-client recovery reads, not reintroduce active Helper
    # search through PostgreSQL.
    assert "_db.search_deleted_clients(" in source
    assert "_db.get_deleted_client_detail(" in source
    assert "_db.search_client_details(" not in source
    assert "PostgreSQL archive OK" in source
    assert "interactive Helper VPN work stays direct to RouterOS" in source
    assert source.index("Preflight: importing FastAPI") < source.index("Stopping ${SERVICE}")

    print("SERVER_RECOVERY_PREFLIGHT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
