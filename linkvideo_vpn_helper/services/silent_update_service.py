from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from linkvideo_vpn_helper.version import APP_VERSION

TASK_NAME = "LinkVideo.Helper Silent Update"
UPDATER_EXE = "LinkVideo.Helper.Updater.exe"
STATE_DIR_NAME = "LinkVideo.Helper"


def _program_files_dir() -> Path:
    base = os.environ.get("ProgramFiles") or r"C:\Program Files"
    return Path(base) / "LinkVideo.Helper"


def _state_dir() -> Path:
    base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return Path(base) / STATE_DIR_NAME / "Updates"


def _creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def task_exists() -> bool:
    if os.name != "nt":
        return False
    updater = _program_files_dir() / UPDATER_EXE
    if not updater.is_file():
        return False
    try:
        result = subprocess.run(
            ["schtasks.exe", "/Query", "/TN", TASK_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            creationflags=_creationflags(),
        )
        return result.returncode == 0
    except Exception:
        return False


def can_use_silent_patches() -> bool:
    return os.name == "nt" and task_exists()


def stage_patch(downloaded_path: Path, *, to_version: str, sha256: str) -> Path:
    """Stage an already SHA-checked official patch for the privileged updater.

    The request file is deliberately not trusted by the privileged updater. It
    re-loads the public GitHub manifest and verifies the staged EXE against the
    authoritative patch SHA before execution.
    """
    if not can_use_silent_patches():
        raise RuntimeError("Фоновый updater LinkVideo.Helper не установлен")
    source = Path(downloaded_path)
    if not source.is_file():
        raise FileNotFoundError(source)

    root = _state_dir()
    root.mkdir(parents=True, exist_ok=True)
    patch = root / "pending-patch.exe"
    temp_patch = root / "pending-patch.exe.new"
    temp_patch.unlink(missing_ok=True)
    shutil.copy2(source, temp_patch)
    temp_patch.replace(patch)

    request = {
        "format": 1,
        "from_version": APP_VERSION,
        "to_version": str(to_version),
        "patch_file": patch.name,
        "sha256": str(sha256 or "").lower(),
    }
    request_path = root / "pending.json"
    request_temp = root / "pending.json.new"
    request_temp.write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    request_temp.replace(request_path)
    return request_path


def has_staged_patch() -> bool:
    root = _state_dir()
    return (root / "pending.json").is_file() and (root / "pending-patch.exe").is_file()


def trigger_staged_patch() -> bool:
    """Start the pre-registered SYSTEM task. No UAC prompt is generated here."""
    if os.name != "nt" or not has_staged_patch() or not task_exists():
        return False
    try:
        result = subprocess.run(
            ["schtasks.exe", "/Run", "/TN", TASK_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            creationflags=_creationflags(),
        )
        return result.returncode == 0
    except Exception:
        return False
