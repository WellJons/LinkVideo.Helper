from __future__ import annotations

"""Explicit opt-in full VPN backup as a Windows Scheduled Task.

The GUI has no background threads that can silently export secrets after
closing. A Windows SYSTEM task runs this exact, already installed Helper EXE
headlessly every 5 minutes. Settings are sealed with Windows DPAPI machine
scope *and* protected with Administrators/SYSTEM-only NTFS ACLs.

This is an explicit full recovery copy: the target Google spreadsheet contains
PPP passwords, NAT configuration and RouterOS recovery snapshots. Only grant
spreadsheet access to authorised recovery administrators.
"""

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

TASK_NAME = "LinkVideo.Helper.VPNSheetsMirror"
STAGING_NAME = "LinkVideo.Helper"  # user-private LOCALAPPDATA
SERVER_DIRECTORY = "VPNSheetsMirror"
CONFIG_NAME = "config.json"
SCHEMA = 1
_INTERVAL = 5


def _on_windows() -> bool:
    return os.name == "nt"


def _data_dir() -> Path:
    if not _on_windows() or not os.environ.get("PROGRAMDATA"):
        raise RuntimeError("Фоновая синхронизация поддерживается только на Windows")
    return Path(os.environ["PROGRAMDATA"]) / "LinkVideo.Helper" / SERVER_DIRECTORY


def _config_path() -> Path:
    return _data_dir() / CONFIG_NAME


def _is_elevated() -> bool:
    if not _on_windows():
        return False
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi(payload: bytes, *, protect: bool) -> bytes:
    """Encrypt/decrypt across the ordinary user -> SYSTEM task boundary.

    The LocalMachine flag allows SYSTEM to decrypt an operator-created blob;
    the NTFS ACL is critical because LocalMachine encryption alone does not
    prevent other local users from decrypting a blob they can read.
    """
    if not _on_windows():
        raise RuntimeError("Windows DPAPI недоступен")
    data = ctypes.create_string_buffer(payload)
    inp = _DataBlob(len(payload), ctypes.cast(data, ctypes.POINTER(ctypes.c_byte)))
    out = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    # CRYPTPROTECT_LOCAL_MACHINE for protection, UI_FORBIDDEN in both paths.
    if protect:
        result = crypt32.CryptProtectData(
            ctypes.byref(inp), None, None, None, None, 0x5, ctypes.byref(out)
        )
    else:
        result = crypt32.CryptUnprotectData(
            ctypes.byref(inp), None, None, None, None, 0x1, ctypes.byref(out)
        )
    if not result:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _pack_settings(payload: dict) -> dict:
    return {
        "schema": SCHEMA,
        "sealed": base64.b64encode(
            _dpapi(json.dumps(payload, ensure_ascii=False).encode("utf-8"), protect=True)
        ).decode("ascii"),
    }


def _unpack_settings(config: dict) -> dict:
    if int(config.get("schema", -1)) != SCHEMA:
        raise ValueError("Неподдерживаемый формат настроек фоновой синхронизации")
    blob = base64.b64decode(config["sealed"], validate=True)
    payload = json.loads(_dpapi(blob, protect=False).decode("utf-8"))
    _validate_settings(payload)
    return payload


def _validate_settings(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Некорректная конфигурация")
    for key in ("routeros_username", "routeros_password"):
        if not str(payload.get(key, "")).strip():
            raise ValueError("Не заданы учётные данные RouterOS")
    google = payload.get("google_service_account")
    if not isinstance(google, dict) or google.get("type") != "service_account" or not all(
        str(google.get(k, "")).strip() for k in ("client_email", "private_key", "token_uri")
    ):
        raise ValueError("Нужен действительный Google Service Account JSON")
    servers = payload.get("servers")
    if not isinstance(servers, list) or not 1 <= len(servers) <= 100:
        raise ValueError("Выберите от 1 до 100 VPN-серверов")
    unique = set()
    for server in servers:
        host = str(server).strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,252}", host) or ".." in host or host.endswith("."):
            raise ValueError("Неверный адрес VPN-сервера")
        if host in unique:
            raise ValueError("VPN-серверы не должны повторяться")
        unique.add(host)
    spreadsheet_id = str(payload.get("spreadsheet_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{15,120}", spreadsheet_id):
        raise ValueError("Некорректный ID Google-таблицы")


def stage_configuration(credentials, google_service_account: dict, servers: list[str], spreadsheet_id: str) -> Path:
    """Write only DPAPI ciphertext in a per-user staging directory.

    No password, API key, or Google private key appears in the command line,
    Task Scheduler definition or readable application settings.
    """
    payload = {
        "routeros_username": str(credentials.username),
        "routeros_password": str(credentials.password),
        "routeros_port": int(credentials.port),
        "routeros_timeout": float(credentials.timeout),
        "google_service_account": google_service_account,
        "servers": list(dict.fromkeys(str(x).strip().lower() for x in servers)),
        "spreadsheet_id": str(spreadsheet_id).strip(),
    }
    _validate_settings(payload)
    base = os.getenv("LOCALAPPDATA")
    if not base:
        raise RuntimeError("Не найден каталог LOCALAPPDATA")
    location = Path(base) / STAGING_NAME / SERVER_DIRECTORY
    location.mkdir(parents=True, exist_ok=True)
    target = location / f"pending-{uuid.uuid4().hex}.json"
    try:
        target.write_text(json.dumps(_pack_settings(payload)), encoding="utf-8")
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _secure_directory(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    _run_command(
        ["icacls.exe", str(folder), "/inheritance:r", "/grant:r",
         "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"]
    )


def _run_command(command: list[str], *, tolerate_failure: bool = False) -> subprocess.CompletedProcess:
    kwargs = {"capture_output": True, "text": True, "timeout": 45}
    if _on_windows():
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(command, **kwargs)
    if result.returncode and not tolerate_failure:
        # Do not include stdout/stderr: RouterOS/Google secrets and task
        # arguments should never end up in error reporting or GUI logs.
        raise RuntimeError(f"Операция Windows завершилась с кодом {result.returncode}")
    return result


def _installed_executable() -> Path:
    current = Path(sys.executable).resolve()
    if not getattr(sys, "frozen", False) or current.name.lower() != "linkvideo.helper.exe":
        raise RuntimeError("Включение фонового режима доступно только в установленном LinkVideo.Helper.exe")
    return current


def _admin_install(staged_path: Path) -> int:
    if not _is_elevated():
        return 3
    # Untrusted staging content is only DPAPI ciphertext. Validate its
    # decrypted schema before making any persistent changes.
    try:
        source = staged_path.resolve(strict=True)
        if not source.name.startswith("pending-") or source.suffix != ".json":
            raise ValueError("Неверное имя временной конфигурации")
        sealed = json.loads(source.read_text(encoding="utf-8"))
        _unpack_settings(sealed)
        folder = _data_dir()
        _secure_directory(folder)
        target = _config_path()
        temp = folder / ("pending-" + uuid.uuid4().hex + ".tmp")
        try:
            temp.write_text(json.dumps(sealed), encoding="utf-8")
            os.replace(temp, target)
            _run_command(
                ["icacls.exe", str(target), "/inheritance:r", "/grant:r",
                 "*S-1-5-18:F", "*S-1-5-32-544:F"]
            )
        finally:
            temp.unlink(missing_ok=True)

        executable = _installed_executable()
        action = f'"{executable}" --vpn-background-worker'
        # schtasks runs a new short-lived worker every 5 minutes as SYSTEM.
        # There is no persistent process holding Program Files open during
        # future Helper upgrades. The GUI can be closed at any time.
        _run_command([
            "schtasks.exe", "/Create", "/TN", TASK_NAME,
            "/SC", "MINUTE", "/MO", str(_INTERVAL),
            "/RU", "SYSTEM", "/RL", "HIGHEST", "/TR", action, "/F",
        ])
        _run_command(["schtasks.exe", "/Run", "/TN", TASK_NAME])
        source.unlink(missing_ok=True)
        return 0
    except Exception:
        return 2


def _admin_remove() -> int:
    if not _is_elevated():
        return 3
    try:
        _run_command(["schtasks.exe", "/End", "/TN", TASK_NAME], tolerate_failure=True)
        _run_command(["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"], tolerate_failure=True)
        _config_path().unlink(missing_ok=True)
        return 0
    except Exception:
        return 2


def task_registered() -> bool:
    if not _on_windows():
        return False
    try:
        return _run_command(
            ["schtasks.exe", "/Query", "/TN", TASK_NAME], tolerate_failure=True
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _elevate(arguments: str) -> int:
    if not _on_windows():
        raise RuntimeError("Поддерживается только Windows")
    from ctypes import wintypes

    class _ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HANDLE),
            ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    exe = _installed_executable()
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = str(exe)
    info.lpParameters = arguments
    info.lpDirectory = tempfile.gettempdir()
    info.nShow = 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        raise RuntimeError("Подтверждение прав администратора отменено либо не выполнено")
    try:
        # The user can decide on UAC. Keep waiting on a UI worker thread,
        # never on the main QApplication thread.
        wait = ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 240000)
        if wait != 0:
            raise RuntimeError("Установка фонового режима не завершилась вовремя")
        code = wintypes.DWORD()
        if not ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise ctypes.WinError()
        return int(code.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(info.hProcess)


def enable_task(staged_file: Path) -> None:
    result = _elevate(f'--vpn-background-install "{staged_file}"')
    if result != 0 or not task_registered():
        raise RuntimeError("Не удалось включить фоновый режим. Проверьте права администратора и планировщик Windows.")


def disable_task() -> None:
    result = _elevate("--vpn-background-remove")
    if result != 0:
        raise RuntimeError("Не удалось отключить фоновый режим через планировщик Windows.")


def run_worker() -> int:
    """Single short-lived headless full backup, never a GUI Qt process."""
    try:
        config = json.loads(_config_path().read_text(encoding="utf-8"))
        payload = _unpack_settings(config)
    except Exception:
        return 2

    # Install in exactly the same order as the desktop app. These wrappers
    # preserve the A:T restoration schema, the deleted-client archive and
    # Google API retry/partial write safeguards.
    from linkvideo_vpn_helper.services.vpn_sheets_retention_compat import install_vpn_sheets_retention_compat
    from linkvideo_vpn_helper.services.vpn_sheets_resilience import install_vpn_sheets_resilience
    from linkvideo_vpn_helper.services.vpn_sheets_operator_view import install_vpn_sheets_operator_view

    install_vpn_sheets_retention_compat()
    install_vpn_sheets_resilience()
    install_vpn_sheets_operator_view()

    from linkvideo_vpn_helper.services.vpn_sheets_sync import GoogleSheetsBackend, VPNSheetsSyncService
    from linkvideo_vpn_helper.services.vpn_service import SessionCredentials, VPNService

    creds = SessionCredentials(
        payload["routeros_username"], payload["routeros_password"],
        int(payload.get("routeros_port", 8728)),
        float(payload.get("routeros_timeout", 6)),
    )
    backend = GoogleSheetsBackend(
        payload["google_service_account"],
        spreadsheet_id=payload["spreadsheet_id"],
    )
    service = VPNSheetsSyncService(VPNService(), backend)
    failures = 0
    for server in payload["servers"]:
        try:
            result = service.sync_server(
                server, creds, source="LinkVideo.Helper background",
                initiator="scheduled-sync",
            )
            # Only aggregate counts reach the log; NEVER client credentials.
            print(f"{server}: clients={result.clients}, added={result.added}, "
                  f"changed={result.changed}, deleted={result.deleted}", flush=True)
        except Exception as exc:
            failures += 1
            print(f"{server}: {type(exc).__name__} (сверка не выполнена)", file=sys.stderr, flush=True)
    return 1 if failures else 0


def dispatch_command(argv: list[str]) -> int | None:
    if "--vpn-background-worker" in argv:
        return run_worker()
    if "--vpn-background-install" in argv:
        position = argv.index("--vpn-background-install")
        if position + 1 >= len(argv):
            return 3
        return _admin_install(Path(argv[position + 1]))
    if "--vpn-background-remove" in argv:
        return _admin_remove()
    return None
