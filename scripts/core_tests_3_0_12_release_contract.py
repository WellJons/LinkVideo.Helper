from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    version = (ROOT / "linkvideo_vpn_helper" / "version.py").read_text(encoding="utf-8")
    notes = (ROOT / "RELEASE_3.0.12_RU.txt").read_text(encoding="utf-8")
    update = (ROOT / "linkvideo_vpn_helper" / "services" / "update_service.py").read_text(encoding="utf-8")

    assert 'APP_VERSION = "3.0.12"' in version
    assert "CERTIFICATE_VERIFY_FAILED" in notes
    assert "3.0.11 -> 3.0.12" in notes
    assert "certifi" in update
    assert "ssl.create_default_context" in update
    assert "ssl.CERT_REQUIRED" in update
    assert "CERT_NONE" not in update

    print("CORE TESTS 3.0.12 RELEASE CONTRACT OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
