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

# Main production channel. As with LinkVideo.Monitor, source code remains in a
# private repository while installers/manifests live in a separate public repo.
GITHUB_UPDATE_MANIFEST_URL = (
    "https://raw.githubusercontent.com/WellJons/LinkVideo.Helper.Updates/main/update-manifest.json"
)

# Transition/fallback channel. GitHub is authoritative for 3.0.10+; Drive stays
# available as an emergency fallback while the migrated fleet settles.
LEGACY_GOOGLE_DRIVE_MANIFEST_URL = (
    "https://drive.google.com/uc?export=download&id=1uHMqX7hyyERZRhOPG7jojcVKaa5e2AOR"
)

# Backward compatibility for code/tests that imported the old constant.
UPDATE_MANIFEST_URL = GITHUB_UPDATE_MANIFEST_URL


@dataclass(slots=True)
class UpdateInfo:
    has_update: bool
    current_version: str
    latest_version: str
    setup_url: str
    notes: str
    sha256: str = ""
    source: str = ""
    artifact_kind: str = "setup"  # setup | patch

    @property
    def is_patch(self) -> bool:
        return self.artifact_kind == "patch"


def _version_tuple(value: str) -> tuple[int, ...]:
    """Parse Windows/release versions robustly and make 2.1.0 == 2.1.0.0."""
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


def _validate_sha256(value: str, *, field_name: str = "sha256") -> str:
    value = str(value or "").strip().lower()
    if value and not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RuntimeError(f"В манифесте обновления указан некорректный {field_name}")
    return value


def _windows_product_version(path: Path) -> str:
    """Read ProductVersion without passing a filesystem path through -Command.

    Windows PowerShell 5.1 parses tokens following ``-Command <script>`` as more
    PowerShell syntax in this launch shape.  That was the 2.0.2 updater failure:
    a downloaded ``C:\\...\\Setup.exe.download`` path became a ParserError before
    the new installer could start.  The path now travels only through the child
    process environment, so spaces, non-ASCII user names and the .download
    suffix cannot change the command grammar.
    """
    if os.name != "nt":
        return ""

    env = os.environ.copy()
    env["LINKVIDEO_UPDATE_FILE"] = str(Path(path))
    script = (
        "$ErrorActionPreference='Stop';"
        "$p=[Environment]::GetEnvironmentVariable('LINKVIDEO_UPDATE_FILE');"
        "if([string]::IsNullOrWhiteSpace($p)){throw 'update file path is empty'};"
        "$v=(Get-Item -LiteralPath $p).VersionInfo.ProductVersion;"
        "[Console]::Out.Write([string]$v)"
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=env,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").replace("\x00", "").strip()
        if detail:
            detail = detail.splitlines()[0][:180]
            raise RuntimeError(f"Не удалось определить версию скачанного установщика ({detail})")
        raise RuntimeError("Не удалось определить версию скачанного установщика")
    return (result.stdout or "").replace("\x00", "").strip()


class UpdateService:
    def __init__(
        self,
        manifest_url: str | None = None,
        *,
        fallback_manifest_url: str | None = None,
    ):
        # Supplying manifest_url explicitly creates a single-channel service,
        # which is useful for tests and diagnostics. The default production
        # service is GitHub-first with Google Drive fallback.
        if manifest_url:
            self.channels = [("custom", manifest_url)]
            if fallback_manifest_url:
                self.channels.append(("fallback", fallback_manifest_url))
        else:
            self.channels = [
                ("github", GITHUB_UPDATE_MANIFEST_URL),
                ("google_drive", fallback_manifest_url or LEGACY_GOOGLE_DRIVE_MANIFEST_URL),
            ]
        self.manifest_url = self.channels[0][1]

    def _load_manifest(self, url: str) -> dict:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": f"LinkVideo.Helper/{APP_VERSION}",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8-sig")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise RuntimeError("Манифест обновления должен содержать JSON-объект")
        return data

    def _parse_manifest(self, data: dict, *, source: str) -> UpdateInfo:
        latest = str(data.get("version", "")).strip()
        # GitHub channel uses download_url like LinkVideo.Monitor. Legacy Drive
        # manifests use url. Accept both during and after migration.
        full_setup_url = str(data.get("download_url") or data.get("url") or "").strip()
        notes = str(data.get("notes", "")).strip()
        full_setup_hash = _validate_sha256(data.get("sha256", ""))

        if not latest:
            raise RuntimeError("В файле обновления не указана версия")

        has_update = _version_tuple(latest) > _version_tuple(APP_VERSION)
        selected_url = full_setup_url
        selected_hash = full_setup_hash
        artifact_kind = "setup"

        # Optional differential patch. The public manifest may contain:
        # "patches": {
        #   "3.0.10": {"url": "...exe", "sha256": "..."}
        # }
        # The patch is selected only for an exact current-version match. Any
        # other client automatically receives the full setup, so old versions
        # can never be stranded by a missing patch.
        patches = data.get("patches") or {}
        if has_update and isinstance(patches, dict):
            patch = patches.get(APP_VERSION)
            if isinstance(patch, dict):
                patch_url = str(patch.get("download_url") or patch.get("url") or "").strip()
                patch_hash = _validate_sha256(patch.get("sha256", ""), field_name="patch sha256")
                if patch_url:
                    selected_url = patch_url
                    selected_hash = patch_hash
                    artifact_kind = "patch"

        if has_update and not selected_url:
            raise RuntimeError("Для новой версии не указана ссылка на установщик или патч")

        return UpdateInfo(
            has_update,
            APP_VERSION,
            latest,
            selected_url,
            notes,
            selected_hash,
            source,
            artifact_kind,
        )

    def check(self) -> UpdateInfo:
        errors: list[str] = []
        for source, url in self.channels:
            try:
                return self._parse_manifest(self._load_manifest(url), source=source)
            except Exception as exc:
                errors.append(f"{source}: {exc}")
        raise RuntimeError(
            "Не удалось проверить обновления ни через основной, ни через резервный канал. "
            + " | ".join(errors)
        )

    def download_setup(
        self,
        setup_url: str,
        progress_callback=None,
        *,
        expected_sha256: str = "",
        expected_version: str = "",
        artifact_kind: str = "",
    ) -> Path:
        # Older UI code does not pass artifact_kind yet. Infer a differential
        # patch from the published asset name so current MainWindow integration
        # remains compatible with both full and differential updates.
        if artifact_kind not in {"setup", "patch"}:
            artifact_kind = "patch" if re.search(r"(?:^|[/_.-])patch(?:[/_.-]|$)", setup_url, re.I) else "setup"
        target_dir = Path(tempfile.gettempdir()) / "LinkVideoHelperUpdate"
        target_dir.mkdir(parents=True, exist_ok=True)
        basename = "LinkVideo.Helper_Patch_Update.exe" if artifact_kind == "patch" else "LinkVideo.Helper_Setup_Update.exe"
        target_file = target_dir / basename
        temp_file = target_dir / (basename + ".download")
        temp_file.unlink(missing_ok=True)

        request = urllib.request.Request(
            setup_url,
            headers={"User-Agent": f"LinkVideo.Helper/{APP_VERSION}", "Cache-Control": "no-cache"},
        )
        try:
            if progress_callback:
                progress_callback(0)
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
                            progress_callback(min(99, int(done * 100 / total)))

            if not temp_file.exists() or temp_file.stat().st_size < 64 * 1024:
                raise RuntimeError("Сервер обновлений вернул слишком маленький файл")
            with temp_file.open("rb") as handle:
                if handle.read(2) != b"MZ":
                    raise RuntimeError("По ссылке обновления получен не Windows EXE-файл")

            expected_hash = _validate_sha256(expected_sha256)
            if expected_hash:
                actual_hash = _sha256(temp_file)
                if actual_hash != expected_hash:
                    raise RuntimeError(
                        "Проверка целостности обновления не пройдена. "
                        "SHA-256 скачанного файла отличается от манифеста."
                    )

            # Full installers carry ProductVersion and are checked against the
            # target release. Differential patch executables are authenticated
            # by SHA-256 and validate their own from/to versions when executed.
            if artifact_kind == "setup" and expected_version and os.name == "nt":
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
        """Launch the GUI installer without ever creating an intermediate console."""
        setup_path = Path(setup_path)
        if not setup_path.exists():
            raise FileNotFoundError(f"Файл обновления не найден: {setup_path}")

        if os.name == "nt":
            try:
                os.startfile(str(setup_path))
            except OSError:
                flags = (
                    int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    | int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                    | int(getattr(subprocess, "DETACHED_PROCESS", 0))
                )
                subprocess.Popen([str(setup_path)], shell=False, creationflags=flags)
        else:
            subprocess.Popen([str(setup_path)], shell=False)
        time.sleep(1.0)
        os._exit(0)
