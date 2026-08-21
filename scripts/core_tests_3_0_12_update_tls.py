from __future__ import annotations

import ssl
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPDATE = ROOT / "linkvideo_vpn_helper" / "services" / "update_service.py"
REQ = ROOT / "requirements.txt"


def main() -> int:
    source = UPDATE.read_text(encoding="utf-8")
    requirements = REQ.read_text(encoding="utf-8")

    required = (
        "import certifi",
        "def _update_tls_context() -> ssl.SSLContext:",
        "ssl.create_default_context()",
        "context.load_default_certs(ssl.Purpose.SERVER_AUTH)",
        "context.load_verify_locations(cafile=bundle)",
        "context.check_hostname = True",
        "context.verify_mode = ssl.CERT_REQUIRED",
        "def _urlopen_verified(request, *, timeout: float):",
        "with _urlopen_verified(request, timeout=15) as response:",
        "with _urlopen_verified(request, timeout=60) as response:",
        "Проверка сертификатов в Helper не отключается",
    )
    for fragment in required:
        assert fragment in source, fragment

    forbidden = (
        "ssl._create_unverified_context",
        "CERT_NONE",
        "check_hostname = False",
    )
    for fragment in forbidden:
        assert fragment not in source, fragment

    assert "certifi>=" in requirements

    # Import the real helper and verify the effective context remains strict.
    from linkvideo_vpn_helper.services.update_service import _update_tls_context

    context = _update_tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert len(context.get_ca_certs()) > 0

    print("CORE TESTS 3.0.12 UPDATE TLS OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
