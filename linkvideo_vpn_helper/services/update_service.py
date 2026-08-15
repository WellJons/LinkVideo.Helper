from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from linkvideo_vpn_helper.version import APP_VERSION

UPDATE_MANIFEST_URL = "https://drive.google.com/uc?export=download&id=1uHMqX7hyyERZRhOPG7jojcVKaa5e2AOR"


@dataclass(slots=True)
class UpdateInfo:
    has_update: bool
    current_version: str
    latest_version: str
    setup_url: str
    notes: str
    sha256: str = ""


def _version_tuple(value: str) -> tuple[int, ...]:
    """Parse Windows/release versions robustly and make 2.1.0 == 2.1.0.0.

    Windows VersionInfo may occasionally return a string with embedded NUL or
    other metadata.  Extract only the leading numeric version instead of
    comparing the raw ProductVersion string.
    """
    clean = str(value or "").replace("\x00", "").strip()
    match = re.search(r"\d+(?:\.\d+){0,3}", clean)
    if not match:
        return ()
    out = [int(part) for part in match.group(0).split(".")]
    while len(out) > 1 and out[-1] == 0:
        out.pop()
    return tuple(out)


def _same_version(left: str, right: str) -> bool:
    return _version_tuple(left) == _version_tuple(right)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _windows_product_version(path: Path) -> str:
    """Read ProductVersion from an EXE without third-party dependencies."""
    if os.name != "nt":
        return ""
    script = (
        "$ErrorActionPreference='Stop';"
        "$p=(Get-Item -LiteralPath $args[0]).VersionInfo.ProductVersion;"
        "[Console]::Out.Write([string]$p)"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError("Не удалось проверить версию скачанного установщика: " + (result.stderr or "PowerShell error").strip())
    return (result.stdout or "").replace("\x00", "").strip()


class UpdateService:
    def __init__(self, manifest_url: str = UPDATE_MANIFEST_URL):
        self.manifest_url = manifest_url

    def check(self) -> UpdateInfo:
        request = urllib.request.Request(
            self.manifest_url,
            headers={"User-Agent": f"LinkVideo.Helper/{APP_VERSION}", "Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8-sig")
        data = json.loads(raw)
        latest = str(data.get("version", "")).strip()
        setup_url = str(data.get("url", "")).strip()
        notes = str(data.get("notes", "")).strip()
        expected_hash = str(data.get("sha256", "")).strip().lower()

        if not latest:
            raise RuntimeError("В файле обновления не указана версия")
        has_update = _version_tuple(latest) > _version_tuple(APP_VERSION)
        if has_update and not setup_url:
            raise RuntimeError("Для новой версии не указана ссылка на установщик")
        if expected_hash and not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise RuntimeError("В version.json указан некорректный SHA-256 установщика")
        return UpdateInfo(has_update, APP_VERSION, latest, setup_url, notes, expected_hash)

    def download_setup(
        self,
        setup_url: str,
        progress_callback=None,
        *,
        expected_sha256: str = "",
        expected_version: str = "",
    ) -> Path:
        target_dir = Path(tempfile.gettempdir()) / "LinkVideoHelperUpdate"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / "LinkVideo.Helper_Setup_Update.exe"
        temp_file = target_dir / "LinkVideo.Helper_Setup_Update.exe.download"
        temp_file.unlink(missing_ok=True)

        request = urllib.request.Request(
            setup_url,
            headers={"User-Agent": f"LinkVideo.Helper/{APP_VERSION}", "Cache-Control": "no-cache"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                total = int(response.headers.get("Content-Length", 0) or 0)
                done = 0
                with temp_file.open("wb") as handle:
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        if progress_callback and total:
                            progress_callback(min(100, int(done * 100 / total)))

            if not temp_file.exists() or temp_file.stat().st_size < 64 * 1024:
                raise RuntimeError("Google Drive вернул слишком маленький файл вместо установщика")
            with temp_file.open("rb") as handle:
                if handle.read(2) != b"MZ":
                    raise RuntimeError("По ссылке обновления получен не Windows EXE-файл")

            expected_hash = str(expected_sha256 or "").strip().lower()
            if expected_hash:
                actual_hash = _sha256(temp_file)
                if actual_hash != expected_hash:
                    raise RuntimeError(
                        "Проверка целостности обновления не пройдена. "
                        "SHA-256 скачанного файла отличается от version.json."
                    )

            if expected_version and os.name == "nt":
                product_version = _windows_product_version(temp_file)
                if not product_version:
                    raise RuntimeError("У скачанного установщика отсутствует ProductVersion")
                if not _same_version(product_version, expected_version):
                    raise RuntimeError(
                        f"Скачан установщик версии {product_version}, хотя ожидалась {expected_version}. "
                        "Обновление не будет запущено."
                    )

            temp_file.replace(target_file)
            if progress_callback:
                progress_callback(100)
            return target_file
        except Exception:
            temp_file.unlink(missing_ok=True)
            raise

    def run_setup(self, setup_path: Path) -> None:
        setup_path = Path(setup_path)
        if not setup_path.exists():
            raise FileNotFoundError(f"Файл установщика не найден: {setup_path}")
        subprocess.Popen(
            ["cmd", "/c", "start", "", str(setup_path)],
            shell=False,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        )
        time.sleep(1.5)
        os._exit(0)
