from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

update_path = root / "linkvideo_vpn_helper/services/update_service.py"
ver_path = root / "linkvideo_vpn_helper/version.py"

update_text = update_path.read_text(encoding="utf-8")
ver_text = ver_path.read_text(encoding="utf-8")

m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', ver_text, re.M)
assert m, "APP_VERSION missing"
current_version = tuple(int(x) for x in m.group(1).split("."))
assert current_version >= (3, 0, 8), current_version

assert 'LinkVideo.Helper.Updates/main/update-manifest.json' in update_text
assert 'LEGACY_GOOGLE_DRIVE_MANIFEST_URL' in update_text
assert '("github", GITHUB_UPDATE_MANIFEST_URL)' in update_text
assert '("google_drive", fallback_manifest_url or LEGACY_GOOGLE_DRIVE_MANIFEST_URL)' in update_text
assert 'data.get("download_url") or data.get("url")' in update_text
assert 'patches.get(APP_VERSION)' in update_text
assert 'artifact_kind = "patch"' in update_text

ast.parse(update_text)

# Runtime contract without network access.
spec = importlib.util.spec_from_file_location("lv_update_test", update_path)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

svc = mod.UpdateService("https://example.invalid/manifest.json")
info = svc._parse_manifest(
    {
        "version": "3.0.9",
        "download_url": "https://example.invalid/full.exe",
        "sha256": "a" * 64,
        "patches": {
            "3.0.8": {
                "download_url": "https://example.invalid/patch.exe",
                "sha256": "b" * 64,
            }
        },
    },
    source="github",
)
assert info.has_update
assert info.is_patch
assert info.setup_url.endswith("patch.exe")
assert info.sha256 == "b" * 64
assert "re.search" in update_text

# Primary failure must fall back to the legacy channel.
svc = mod.UpdateService()
svc.channels = [("github", "first"), ("google_drive", "second")]

def fake_load(url: str):
    if url == "first":
        raise OSError("offline")
    return {"version": "3.0.9", "url": "https://example.invalid/setup.exe"}

svc._load_manifest = fake_load
info = svc.check()
assert info.source == "google_drive"
assert info.artifact_kind == "setup"

print("CORE TESTS 3.0.8 GITHUB UPDATE MIGRATION OK")
