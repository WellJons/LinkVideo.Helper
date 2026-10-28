from __future__ import annotations

"""Executable regression tests for the single visible full-Setup update route."""

from linkvideo_vpn_helper.services import update_service

OFFICIAL_SETUP = (
    "https://github.com/WellJons/LinkVideo.Helper.Updates/"
    "releases/download/v3.0.14/LinkVideo.Helper_Setup.exe"
)
OLD_PATCH = (
    "https://github.com/WellJons/LinkVideo.Helper.Updates/"
    "releases/download/v3.0.14/LinkVideo.Helper_Patch_3.0.13_to_3.0.14.exe"
)
SHA = "a" * 64


def manifest(*, old_url: bool = False) -> dict:
    result = {
        "version": "3.0.14",
        "sha256": SHA,
        "notes": "Release notes",
        "patches": {"3.0.13": {"download_url": OLD_PATCH, "sha256": "b" * 64}},
    }
    result["url" if old_url else "download_url"] = OFFICIAL_SETUP
    return result


def main() -> None:
    original = update_service.APP_VERSION
    try:
        service = update_service.UpdateService()
        assert service.manifest_url == update_service.GITHUB_UPDATE_MANIFEST_URL
        assert service.channels[1][1] == update_service.LEGACY_GOOGLE_DRIVE_MANIFEST_URL

        # Exact-version legacy patches are deliberately ignored by NEW helpers.
        for current in ("3.0.8", "3.0.11", "3.0.12", "3.0.13"):
            update_service.APP_VERSION = current
            info = service._parse_manifest(manifest(), source="github")
            assert info.has_update and info.latest_version == "3.0.14"
            assert info.setup_url == OFFICIAL_SETUP
            assert info.sha256 == SHA
            assert info.artifact_kind == "setup"
            assert not info.is_patch

        # The old Drive format still works when GitHub is temporarily offline.
        update_service.APP_VERSION = "3.0.11"
        legacy = service._parse_manifest(manifest(old_url=True), source="google_drive")
        assert legacy.has_update and legacy.setup_url == OFFICIAL_SETUP
        assert legacy.artifact_kind == "setup"

        # An already up-to-date/newer client is never forced to downgrade.
        update_service.APP_VERSION = "3.0.14"
        assert not service._parse_manifest(manifest(), source="github").has_update
        update_service.APP_VERSION = "3.0.15"
        assert not service._parse_manifest(manifest(), source="github").has_update

        update_service.APP_VERSION = "3.0.13"
        no_hash = manifest()
        no_hash.pop("sha256")
        try:
            service._parse_manifest(no_hash, source="github")
        except RuntimeError as exc:
            assert "sha256" in str(exc).lower()
        else:
            raise AssertionError("Missing Setup checksum accepted")

        hostile = manifest()
        hostile["download_url"] = "https://attacker.invalid/installer.exe"
        try:
            service._parse_manifest(hostile, source="github")
        except RuntimeError as exc:
            assert "недоверенный" in str(exc)
        else:
            raise AssertionError("Untrusted download origin accepted")

        # Existing default-channel fallback remains available.
        attempts = []

        def fake_load(url: str) -> dict:
            attempts.append(url)
            if url == update_service.GITHUB_UPDATE_MANIFEST_URL:
                raise ConnectionError("test: GitHub unavailable")
            return manifest(old_url=True)

        service._load_manifest = fake_load
        fallback = service.check()
        assert fallback.has_update and fallback.artifact_kind == "setup"
        assert attempts == [
            update_service.GITHUB_UPDATE_MANIFEST_URL,
            update_service.LEGACY_GOOGLE_DRIVE_MANIFEST_URL,
        ]
    finally:
        update_service.APP_VERSION = original

    print("CORE TESTS: FULL SETUP ONLY AND LEGACY MANIFEST COMPAT OK")


if __name__ == "__main__":
    main()
