from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
version_text = (ROOT / "linkvideo_vpn_helper" / "version.py").read_text(encoding="utf-8")
m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', version_text, re.M)
if not m:
    raise SystemExit("APP_VERSION не найден")
version = m.group(1).strip()
if "-" in version:
    raise SystemExit("Для release-сборки APP_VERSION не должен содержать alpha/beta суффикс")

installer = ROOT / "installer.iss"
text = installer.read_text(encoding="utf-8")
text = re.sub(r'#define MyAppVersion "[^"]+"', f'#define MyAppVersion "{version}"', text, count=1)
parts = [int(x) for x in version.split(".")]
while len(parts) < 4:
    parts.append(0)
winver = ".".join(map(str, parts[:4]))
text = re.sub(r'VersionInfoVersion=.*', f'VersionInfoVersion={winver}', text)
text = re.sub(r'VersionInfoProductVersion=.*', f'VersionInfoProductVersion={version}', text)
installer.write_text(text, encoding="utf-8")

manifest = ROOT / "server_example" / "version.json"
data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
data["version"] = version
manifest.parent.mkdir(parents=True, exist_ok=True)
manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Release version synced: {version}")
