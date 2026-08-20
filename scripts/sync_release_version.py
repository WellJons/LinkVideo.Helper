from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "linkvideo_vpn_helper" / "version.py"

version_text = VERSION_FILE.read_text(encoding="utf-8")
match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', version_text, re.M)
if not match:
    raise SystemExit("APP_VERSION не найден")

version = match.group(1).strip()
if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", version):
    raise SystemExit(f"Некорректный release APP_VERSION: {version!r}")

# APP_VERSION is now the only authoritative version source.  The old Inno
# installer and server_example manifest were retired; generated Windows version
# resources and public update manifests are produced later by their dedicated
# release builders from this same value.
print(f"Release version synced: {version}")
