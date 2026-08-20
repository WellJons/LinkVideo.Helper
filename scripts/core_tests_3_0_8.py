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
current_version_text = m.group(1)
current_version = tuple(int(x) for x in current_version_text.split("."))
assert current_version >= (3, 0, 8), current_version

assert 'LinkVideo.Helper.Updates/main/update-manifest.json' in update_text
assert 'LEGACY_GOOGLE_DRIVE_MANIFEST_URL' in update_text
assert '("github", GITHUB_UPDATE_MANIFEST_URL)' in update_text
assert '("google_drive", fallback_manifest_url or LEGACY_GOOGLE_DRIVE_MANIFEST_URL)' in update_text
assert 'data.get("download_url") or data.get("url")' in update_text
assert 'if _same_version(str(from_version), APP_VERSION)' in update_text
assert 'artifact_kind = "patch"' in update_text
assert "required=True" in update_text
assert "_MAX_SETUP_BYTES" in update_text

ast.parse(update_text)

# Runtime contract without network access. The test target is always one patch
# release newer than the version currently being built, so this regression does
# not become stale on every release bump.
spec = importlib.util.spec_from_file_location("lv_update_test", update_path)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

target_parts = list(current_version)
while len(target_parts) < 3:
    target_parts.append(0)
target_parts[2] += 1
target_version = ".".join(str(x) for x in target_parts[:3])
normalized_patch_key = current_version_text if len(current_version_text.split(".")) == 4 else current_version_text + ".0"

svc = mod.UpdateService("https://example.invalid/manifest.json")
info = svc._parse_manifest(
    {
        "version": target_version,
        "download_url": "https://github.com/WellJons/LinkVideo.Helper.Updates/releases/download/v9.9.9/LinkVideo.Helper_Setup.exe",
        "sha256": "a" * 64,
        "patches": {
            normalized_patch_key: {
                "download_url": "https://github.com/WellJons/LinkVideo.Helper.Updates/releases/download/v9.9.9/LinkVideo.Helper_Patch.exe",
                "sha256": "b" * 64,
            }
        },
    },
    source="github",
)
assert info.has_update
assert info.is_patch
assert info.setup_url.lower().endswith("patch.exe")
assert info.sha256 == "b" * 64
assert "re.search" in update_text

for bad_manifest in (
    {"version": "release-" + target_version, "download_url": "https://github.com/x.exe", "sha256": "a" * 64},
    {"version": target_version, "download_url": "https://github.com/x.exe", "sha256": ""},
    {"version": target_version, "download_url": "http://github.com/x.exe", "sha256": "a" * 64},
):
    try:
        svc._parse_manifest(bad_manifest, source="github")
    except RuntimeError:
        pass
    else:
        raise AssertionError(f"unsafe update manifest was accepted: {bad_manifest!r}")

# Primary failure must fall back to the legacy channel using the same dynamic
# future version.
svc = mod.UpdateService()
svc.channels = [("github", "first"), ("google_drive", "second")]

def fake_load(url: str):
    if url == "first":
        raise OSError("offline")
    return {
        "version": target_version,
        "url": "https://drive.google.com/uc?export=download&id=test",
        "sha256": "c" * 64,
    }

svc._load_manifest = fake_load
info = svc.check()
assert info.source == "google_drive"
assert info.artifact_kind == "setup"
assert info.has_update

print("CORE TESTS GITHUB UPDATE MIGRATION OK")
