from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SETUP = ROOT / "release_upload" / "LinkVideo_VPN_Helper_Setup.exe"
TEMPLATE = ROOT / "server_example" / "version.json"
OUTPUT = ROOT / "release_upload" / "version.json"

from linkvideo_vpn_helper.version import APP_VERSION

if not SETUP.exists():
    raise SystemExit(f"Setup not found: {SETUP}")

data = json.loads(TEMPLATE.read_text(encoding="utf-8")) if TEMPLATE.exists() else {}
url = str(data.get("url", "") or "").strip()
if not url:
    raise SystemExit("server_example/version.json: url is empty")

h = hashlib.sha256()
with SETUP.open("rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        h.update(chunk)

data["version"] = APP_VERSION
data["sha256"] = h.hexdigest().lower()
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Manifest ready: {OUTPUT}")
print(f"Version: {APP_VERSION}")
print(f"SHA256: {data['sha256']}")
