from __future__ import annotations

"""Robust discovery of the Google Sheets service-account JSON.

The installer and older Helper builds have used both ``LinkVideo.Helper`` and
``LinkVideo\Helper`` data directories.  Google also downloads service-account
keys with generated names.  Requiring one exact folder *and* one exact filename
made a valid key look missing.

This compatibility layer treats the file name as irrelevant and validates the
JSON payload instead.  The key is never copied into the application directory,
repository, or executable.
"""

import json
import os
from pathlib import Path
from typing import Any


_INSTALLED = False

_REQUIRED_FIELDS = ("client_email", "private_key", "token_uri")


def _is_service_account(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if str(payload.get("type", "") or "").strip() != "service_account":
        return False
    return all(str(payload.get(name, "") or "").strip() for name in _REQUIRED_FIELDS)


def _load_valid(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.suffix.lower() != ".json":
            return None
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if _is_service_account(payload) else None
    except Exception:
        return None


def candidate_directories() -> list[Path]:
    result: list[Path] = []

    def add(path: Path | None):
        if path is None:
            return
        try:
            path = path.expanduser()
        except Exception:
            pass
        if path not in result:
            result.append(path)

    program_data = str(os.getenv("PROGRAMDATA", "") or "").strip()
    if program_data:
        root = Path(program_data)
        add(root / "LinkVideo" / "Helper")
        add(root / "LinkVideo.Helper")
        add(root / "LinkVideo.Helper" / "Helper")

    app_data = str(os.getenv("APPDATA", "") or "").strip()
    if app_data:
        root = Path(app_data)
        add(root / "LinkVideo" / "Helper")
        add(root / "LinkVideo.Helper")
        add(root / "LinkVideo.Helper" / "Helper")

    local_app_data = str(os.getenv("LOCALAPPDATA", "") or "").strip()
    if local_app_data:
        root = Path(local_app_data)
        add(root / "LinkVideo" / "Helper")
        add(root / "LinkVideo.Helper")
        add(root / "LinkVideo.Helper" / "Helper")

    return result


def discover_service_account_file(settings=None) -> Path | None:
    """Return the first valid service-account JSON from explicit/common paths."""

    explicit: list[Path] = []

    env_file = str(os.getenv("LINKVIDEO_SHEETS_SERVICE_ACCOUNT_FILE", "") or "").strip()
    if env_file:
        explicit.append(Path(os.path.expandvars(os.path.expanduser(env_file))))

    configured = ""
    if settings is not None:
        try:
            configured = str(settings.value("sheets/service_account_file", "", str) or "").strip()
        except Exception:
            configured = ""
    if configured:
        explicit.append(Path(os.path.expandvars(os.path.expanduser(configured))))

    for path in explicit:
        if _load_valid(path) is not None:
            return path

    preferred_names = (
        "google_sheets_service_account.json",
        "service_account.json",
    )
    for directory in candidate_directories():
        for name in preferred_names:
            path = directory / name
            if _load_valid(path) is not None:
                return path

    # Google-generated names such as linkvideo-helper-e4285d....json are fine.
    # Prefer recently modified files if several valid service-account keys exist.
    discovered: list[Path] = []
    for directory in candidate_directories():
        try:
            for path in directory.glob("*.json"):
                if _load_valid(path) is not None:
                    discovered.append(path)
        except Exception:
            continue
    if not discovered:
        return None
    try:
        discovered.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        pass
    return discovered[0]


def install_google_key_discovery() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.services.vpn_sheets_sync import GoogleSheetsBackend

    original = GoogleSheetsBackend.from_settings

    def robust_from_settings(cls, settings=None):
        # Preserve supported raw JSON / exact configured-path behaviour first.
        try:
            backend = original(settings)
            if backend is not None:
                # Existing backend may not know which file supplied it.
                path = discover_service_account_file(settings)
                if path is not None:
                    backend.source_path = str(path)
                return backend
        except Exception:
            pass

        path = discover_service_account_file(settings)
        if path is None:
            return None
        payload = _load_valid(path)
        if payload is None:
            return None
        backend = cls(payload)
        backend.source_path = str(path)
        backend.service_account_email = str(payload.get("client_email", "") or "")
        return backend

    GoogleSheetsBackend.from_settings = classmethod(robust_from_settings)
    _INSTALLED = True
