from __future__ import annotations

"""Cross-version updater contract, download integrity and fallback tests.

All "downloads" are in-memory. No production URL is called and no installer
is launched. Validate upgrade path for older 3.0.x manifests and full setups.
"""
import hashlib
import io
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from linkvideo_vpn_helper.services import update_service as update

base = {
    "version": "3.0.14",
    "download_url": "https://github.com/WellJons/LinkVideo.Helper.Updates/releases/download/v3.0.14/LinkVideo.Helper_Setup.exe",
    "sha256": "a"*64,
    "notes": "Upgrade test",
}
for current in ("3.0.8", "3.0.10", "3.0.11", "3.0.12", "3.0.13"):
    with patch.object(update, "APP_VERSION", current):
        service = update.UpdateService()
        old = dict(base, patches={
            "3.0.11": {
                "download_url": "https://github.com/WellJons/LinkVideo.Helper.Updates/releases/download/v3.0.14/old-patch.exe",
                "sha256": "b"*64,
            }
        })
        info = service._parse_manifest(old, source="github")
        assert info.has_update and info.artifact_kind == "setup", current
        assert info.setup_url == base["download_url"], current
        assert info.sha256 == "a"*64
        with patch.object(service, "_load_manifest", return_value=old):
            assert service.check().has_update
for current in ("3.0.14", "3.0.15"):
    with patch.object(update, "APP_VERSION", current):
        service = update.UpdateService()
        assert not service._parse_manifest(base, source="github").has_update

with patch.object(update, "APP_VERSION", "3.0.12"):
    service = update.UpdateService()
    legacy = {
        "version": "3.0.14", "url": base["download_url"],
        "sha256": base["sha256"],
    }
    assert service._parse_manifest(legacy, source="google_drive").has_update

# A malformed primary manifest must fall back; no infinite retry.
service = update.UpdateService(
    manifest_url="https://raw.githubusercontent.com/example/a.json",
    fallback_manifest_url="https://raw.githubusercontent.com/example/b.json",
)
attempts = []
def load(url):
    attempts.append(url)
    if url.endswith("/a.json"):
        raise OSError("primary temporarily offline")
    return base
with patch.object(update, "APP_VERSION", "3.0.12"):
    with patch.object(service, "_load_manifest", side_effect=load):
        assert service.check().source == "fallback"
assert len(attempts) == 2

# Full installer: verify MZ header, version, hash, atomic staging cleanup.
binary = b"MZ" + b"fake-verified-full-exe" * 4096
digest = hashlib.sha256(binary).hexdigest()
class FakeResponse:
    headers = {"Content-Length": str(len(binary))}
    def __enter__(self):
        self.buffer = io.BytesIO(binary)
        return self
    def __exit__(self, *_):
        self.buffer.close()
    def read(self, amount):
        return self.buffer.read(amount)
    def geturl(self):
        return base["download_url"]
with tempfile.TemporaryDirectory() as raw:
    with patch.object(update.tempfile, "gettempdir", return_value=raw):
        with patch.object(update, "_urlopen_verified", return_value=FakeResponse()):
            with patch.object(update, "_windows_product_version", return_value="3.0.14"):
                setup = service.download_setup(
                    base["download_url"], expected_sha256=digest,
                    expected_version="3.0.14", artifact_kind="setup"
                )
                assert setup.is_file()
                assert setup.read_bytes() == binary
        with patch.object(update, "_urlopen_verified", return_value=FakeResponse()):
            try:
                service.download_setup(
                    base["download_url"], expected_sha256="a"*64,
                    expected_version="3.0.14", artifact_kind="setup"
                )
            except RuntimeError as exc:
                assert "SHA-256" in str(exc)
            else:
                raise AssertionError("Corrupted installer checksum accepted")
        assert not (setup.parent / (setup.name + ".download")).exists()

print("CORE TESTS FULL SETUP UPGRADE FROM 3.0.8–3.0.13 AND INTEGRITY OK")
