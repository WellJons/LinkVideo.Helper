from __future__ import annotations

"""Fix ProductVersion probing for downloaded update EXEs on Windows.

Windows PowerShell 5.1 treats tokens after ``-Command <script>`` as part of the
command text in this invocation shape. The legacy updater passed the downloaded
file path after the script and PowerShell tried to parse a path such as
``C:/Users/.../LinkVideo.Helper_Setup_Update.exe.download`` as PowerShell code.

Pass the path through an environment variable instead. This works with spaces,
non-ASCII user names and Windows PowerShell 5.1 without shell quoting.
"""

import os
import subprocess
from pathlib import Path


_INSTALLED = False


def _windows_product_version(path: Path) -> str:
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
        # Do not expose a page of mojibake from Windows PowerShell stderr in a
        # toast. Full process details remain available to the application log.
        detail = (result.stderr or "").replace("\x00", "").strip()
        if detail:
            detail = detail.splitlines()[0][:180]
            raise RuntimeError(f"Не удалось определить версию скачанного установщика ({detail})")
        raise RuntimeError("Не удалось определить версию скачанного установщика")
    return (result.stdout or "").replace("\x00", "").strip()


def install_update_version_probe_compat() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    import linkvideo_vpn_helper.services.update_service as update_service

    update_service._windows_product_version = _windows_product_version
    _INSTALLED = True
