from __future__ import annotations

"""Machine-level Telegram task configuration, separate from desktop sessions.

The bot runs in a scheduled startup task under LocalSystem. Credentials are
encrypted with Windows DPAPI machine scope AND file ACLs restrict reading to
SYSTEM/Administrators. No secrets are passed through argv or QSettings.
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

from linkvideo_vpn_helper.services.telegram_invites import (
    InviteStore, invitation_url, state_database_path,
)

TASK = "LinkVideo.Helper.TelegramBot"
_URL = re.compile(r"https://t\.me/([a-zA-Z][a-zA-Z0-9_]{4,31})/?\Z")
_TOKEN = re.compile(r"[0-9]{6,14}:[A-Za-z0-9_-]{20,}\Z")
CREATE_NO_WINDOW = 0x08000000


def bot_state_dir() -> Path:
    return state_database_path().parent


def config_file() -> Path:
    return bot_state_dir() / "secrets" / "config.json"


def public_file() -> Path:
    return bot_state_dir() / "bot_public.json"


def clean_bot_link(value: str) -> str:
    raw = str(value or "").strip()
    if raw.startswith("@"):
        raw = "https://t.me/" + raw[1:]
    elif raw and not raw.lower().startswith("https://"):
        raw = "https://t.me/" + raw
    match = _URL.fullmatch(raw)
    if not match:
        raise ValueError("Укажите ссылку вида https://t.me/LinkVideoHelperBot.")
    return "https://t.me/" + match.group(1)


def _run_hidden(argv: list[str], *, timeout: int = 18) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _restrict(path: Path, *, members_can_read: bool):
    """Limit access by well-known SIDs (language-independent icacls)."""
    if os.name != "nt":
        raise RuntimeError("Служба поддерживается только на Windows.")
    # icacls SID syntax uses * before SID; names depend on Windows locale.
    prefix = "(OI)(CI)" if path.is_dir() else ""
    args = [
        "icacls", str(path), "/inheritance:r", "/grant:r",
        f"*S-1-5-18:{prefix}F", f"*S-1-5-32-544:{prefix}F",
    ]
    if members_can_read:
        args.append(f"*S-1-5-11:{prefix}RX")
    result = _run_hidden(args)
    if result.returncode != 0:
        raise RuntimeError("Не удалось ограничить доступ к конфигурации бота.")


def _protect_machine(value: str) -> str:
    if os.name != "nt":
        raise RuntimeError("DPAPI для бота доступен только на Windows.")
    # Use the machine DPAPI scope for SYSTEM/admin. ACLs limit which users
    # can read the ciphertext, because machine DPAPI alone is not an ACL.
    from ctypes import POINTER, Structure, byref, c_char, create_string_buffer, sizeof

    class Blob(Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", POINTER(c_char))]

    content = value.encode("utf-8")
    buffer = create_string_buffer(content)
    source = Blob(len(content), ctypes.cast(buffer, POINTER(c_char)))
    target = Blob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
        byref(source), None, None, None, None, 0x4, byref(target)
    ):
        raise RuntimeError("Не удалось защитить токен бота через DPAPI.")
    try:
        protected = ctypes.string_at(target.pbData, target.cbData)
        return base64.b64encode(protected).decode("ascii")
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(ctypes.cast(target.pbData, ctypes.c_void_p))


def _unprotect_machine(encoded: str) -> str:
    if os.name != "nt":
        raise RuntimeError("DPAPI доступен только на Windows.")
    from ctypes import POINTER, Structure, byref, c_char, create_string_buffer

    class Blob(Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", POINTER(c_char))]

    try:
        cipher = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise RuntimeError("Конфигурация бота повреждена.") from exc
    buffer = create_string_buffer(cipher)
    source = Blob(len(cipher), ctypes.cast(buffer, POINTER(c_char)))
    target = Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        byref(source), None, None, None, None, 0, byref(target)
    ):
        raise RuntimeError("Не удалось открыть защищённый токен бота.")
    try:
        return ctypes.string_at(target.pbData, target.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def public_bot_link() -> str:
    try:
        return clean_bot_link(json.loads(public_file().read_text("utf-8"))["bot_link"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return ""


def configured() -> bool:
    return config_file().is_file()


def service_token() -> str:
    try:
        data = json.loads(config_file().read_text(encoding="utf-8"))
        token = _unprotect_machine(str(data["token_dpapi_machine"]))
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise RuntimeError("Бот не настроен для службы Windows.") from exc
    if not _TOKEN.fullmatch(token):
        raise RuntimeError("Сохранён некорректный токен Telegram.")
    return token


def _ensure_folders():
    bot_state_dir().mkdir(parents=True, exist_ok=True)
    secret = config_file().parent
    secret.mkdir(parents=True, exist_ok=True)
    _restrict(bot_state_dir(), members_can_read=True)
    _restrict(secret, members_can_read=False)


def _save_link_and_token(link: str, token: str):
    url = clean_bot_link(link)
    if token and not _TOKEN.fullmatch(token):
        raise ValueError("Токен BotFather имеет неправильный формат.")
    _ensure_folders()
    current_cipher = ""
    if config_file().exists():
        try:
            current_cipher = str(json.loads(config_file().read_text("utf-8"))["token_dpapi_machine"])
        except (ValueError, KeyError, OSError):
            current_cipher = ""
    cipher = _protect_machine(token) if token else current_cipher
    if not cipher:
        raise ValueError("При первом сохранении укажите токен BotFather.")
    tmp = config_file().with_suffix(".new")
    tmp.write_text(json.dumps({"token_dpapi_machine": cipher}, ensure_ascii=False), "utf-8")
    _restrict(tmp, members_can_read=False)
    tmp.replace(config_file())
    _restrict(config_file(), members_can_read=False)
    _save_public_link(url)


def _save_public_link(link: str):
    url = clean_bot_link(link)
    _ensure_folders()
    public_file().write_text(
        json.dumps({"bot_link": url}, ensure_ascii=False), "utf-8"
    )
    _restrict(public_file(), members_can_read=True)


def _task_command():
    executable = Path(sys.executable).resolve()
    # Task must launch the installed EXE, NOT a Python script in a temp folder.
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Установка фоновой задачи доступна только из EXE Helper.")
    return f'"{executable}" --telegram-worker'


def install_startup_task():
    if os.name != "nt":
        raise RuntimeError("Автозапуск доступен только в Windows.")
    cmd = _task_command()
    # schtasks does not store the bot token or any credentials in task args.
    result = _run_hidden([
        "schtasks.exe", "/Create", "/F", "/TN", TASK, "/SC", "ONSTART",
        "/RU", "SYSTEM", "/RL", "HIGHEST", "/TR", cmd,
    ])
    if result.returncode:
        raise RuntimeError("Не удалось установить фоновую задачу Windows.")
    started = _run_hidden(["schtasks.exe", "/Run", "/TN", TASK])
    if started.returncode:
        raise RuntimeError("Задача установлена, но пока не запущена.")


def uninstall_startup_task():
    if os.name != "nt":
        raise RuntimeError("Остановка доступна только в Windows.")
    _run_hidden(["schtasks.exe", "/End", "/TN", TASK])
    result = _run_hidden(["schtasks.exe", "/Delete", "/F", "/TN", TASK])
    if result.returncode:
        raise RuntimeError("Не удалось удалить фоновую задачу Windows.")


def is_task_installed() -> bool:
    if os.name != "nt":
        return False
    return _run_hidden(["schtasks.exe", "/Query", "/TN", TASK]).returncode == 0


def administer_operation(source_path: str):
    """Only elevated explicit user actions may change the machine bot."""
    if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("Для настройки службы требуются права администратора.")
    path = Path(source_path).resolve()
    # Payload is short-lived and created by GUI. Do not echo contents in logs.
    if path.stat().st_size > 10000:
        raise RuntimeError("Слишком большой запрос настройки.")
    try:
        data = json.loads(path.read_text("utf-8"))
        operation = str(data.get("operation") or "")
        if operation == "save_link":
            _save_public_link(str(data.get("link") or ""))
            response = "Ссылка на Telegram-бота сохранена."
        elif operation == "configure":
            _save_link_and_token(str(data.get("link") or ""),
                                 str(data.get("token") or ""))
            install_startup_task()
            response = "Настройки сохранены, фоновая задача установлена."
        elif operation == "add":
            user_id = int(data["telegram_id"])
            InviteStore(state_database_path()).grant(user_id)
            response = "Сотрудник добавлен по Telegram ID."
        elif operation == "remove":
            InviteStore(state_database_path()).revoke(int(data["telegram_id"]))
            response = "Доступ сотрудника отозван."
        elif operation == "invite":
            link = public_bot_link()
            if not link:
                raise RuntimeError("Сначала сохраните ссылку бота.")
            secret = InviteStore(state_database_path()).create(hours=24, uses=1)
            response = link + "?start=" + secret
        elif operation == "stop":
            uninstall_startup_task()
            response = "Задача остановлена и удалена."
        else:
            raise ValueError("Неизвестная операция.")
        Path(str(data["result_path"])).write_text(
            json.dumps({"ok": True, "message": response}, ensure_ascii=False),
            "utf-8",
        )
    except Exception:
        # No credential-bearing exceptions / URLs are forwarded.
        try:
            raw = json.loads(path.read_text("utf-8"))
            Path(str(raw["result_path"])).write_text(
                json.dumps({"ok": False, "message": "Не удалось применить настройку. "
                            "Проверьте права администратора, ссылку и данные."},
                           ensure_ascii=False), "utf-8",
            )
        except Exception:
            pass
    finally:
        path.unlink(missing_ok=True)


def admin_action(payload: dict) -> str:
    """Run one privileged task from the settings GUI; never pass secrets in argv."""
    if os.name != "nt":
        raise RuntimeError("Настройка службы поддерживается только в Windows.")
    # Separate GUI and service identity. UAC grants a one-time admin operation.
    with tempfile.TemporaryDirectory(prefix="lv-bot-config-") as td:
        request = Path(td) / "request.json"
        answer = Path(td) / "response.json"
        request.write_text(json.dumps({**payload, "result_path": str(answer)},
                                      ensure_ascii=False), encoding="utf-8")
        # tempfile creates a per-user directory. Do not strip the owner's
        # ACL: the non-elevated GUI must still read the status response.
        # The UAC helper only receives the file path, never the token in argv.
        # Powershell sees only a path; bot token is never supplied as a CLI arg.
        # Start-Process returns a process exit code after the elevated helper.
        script = (
            "$p = Start-Process -FilePath " +
            "'" + str(Path(sys.executable).resolve()).replace("'", "''") + "'" +
            " -ArgumentList " +
            "'--telegram-admin-op', '" + str(request).replace("'", "''") + "'" +
            " -Verb RunAs -PassThru -Wait -WindowStyle Hidden; " +
            "if ($null -eq $p) { exit 2 }; exit $p.ExitCode"
        )
        started = _run_hidden([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script,
        ], timeout=120)
        if started.returncode != 0 or not answer.exists():
            raise RuntimeError("Настройка отменена или не разрешена Windows.")
        try:
            result = json.loads(answer.read_text("utf-8"))
            if not result.get("ok"):
                raise RuntimeError(str(result.get("message") or "Ошибка настройки."))
            return str(result.get("message") or "")
        finally:
            request.unlink(missing_ok=True)
