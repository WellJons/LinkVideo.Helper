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
from xml.sax.saxutils import escape as xml_escape

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
    from ctypes import POINTER, Structure, byref, c_char, create_string_buffer

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
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(ctypes.cast(target.pbData, ctypes.c_void_p))


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


def probe_telegram_bot() -> str:
    """Verify the configured token via Telegram getMe, never log the token."""
    import urllib.request
    token = service_token()
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/getMe",
        headers={"User-Agent": "LinkVideo.Helper/3.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=9) as response:
            result = json.loads(response.read(65536).decode("utf-8"))
    except Exception:
        raise RuntimeError("Telegram API недоступен или токен не принят.") from None
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError("Telegram отклонил сохранённый токен.")
    user = result.get("result")
    username = str(user.get("username") or "") if isinstance(user, dict) else ""
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        raise RuntimeError("Telegram не вернул корректное имя бота.")
    saved = public_bot_link()
    if saved and saved.casefold() != ("https://t.me/" + username).casefold():
        raise RuntimeError("Ссылка не совпадает с ботом сохранённого токена.")
    return "Telegram API отвечает: @" + username


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


def save_service_b2o_credentials(login: str, password: str) -> None:
    """Explicit provision of the bot's local B2O identity (not GUI credentials)."""
    login = str(login or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,100}", login):
        raise ValueError("Неверный логин B2O.")
    if not isinstance(password, str) or not 1 <= len(password) <= 256:
        raise ValueError("Введите пароль B2O для фонового бота.")
    _ensure_folders()
    # Verify with B2O in memory; never persist the API token from the test.
    from linkvideo_vpn_helper.services.archive_service_core import B2OService

    class TempSettings:
        def __init__(self):
            self._data = {}
        def value(self, key, default="", typ=None):
            return self._data.get(key, default)
        def setValue(self, key, value):
            self._data[key] = value
        def remove(self, key):
            self._data.pop(key, None)

    try:
        B2OService(TempSettings()).login(login, password)
    except Exception:
        raise RuntimeError(
            "B2O отклонил логин/пароль или сервер недоступен."
        ) from None
    secret_path = config_file().parent / "b2o_service.json"
    temp = secret_path.with_suffix(".new")
    temp.write_text(json.dumps({
        "username_machine": _protect_machine(login),
        "password_machine": _protect_machine(password),
    }, ensure_ascii=False), encoding="utf-8")
    _restrict(temp, members_can_read=False)
    temp.replace(secret_path)
    _restrict(secret_path, members_can_read=False)


def service_b2o_credentials() -> tuple[str, str]:
    try:
        filename = config_file().parent / "b2o_service.json"
        fields = json.loads(filename.read_text(encoding="utf-8"))
        return (
            _unprotect_machine(str(fields["username_machine"])),
            _unprotect_machine(str(fields["password_machine"])),
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("Авторизация B2O для службы не настроена.") from exc


def service_b2o_configured() -> bool:
    return (config_file().parent / "b2o_service.json").is_file()


def clear_service_b2o_credentials():
    (config_file().parent / "b2o_service.json").unlink(missing_ok=True)


def save_service_vpn_credentials(login: str, password: str) -> None:
    """Store the *bot worker's* MikroTik credential in local machine DPAPI.

    Values are supplied interactively through UAC, never embedded in Python
    source, GitHub, installer, logs or task arguments.
    """
    login = str(login or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{2,100}", login):
        raise ValueError("Неверный логин MikroTik.")
    if not isinstance(password, str) or not 1 <= len(password) <= 256:
        raise ValueError("Укажите пароль MikroTik.")
    _ensure_folders()
    path = config_file().parent / "vpn_service.json"
    pending = path.with_suffix(".new")
    pending.write_text(json.dumps({
        "username_machine": _protect_machine(login),
        "password_machine": _protect_machine(password),
    }, ensure_ascii=False), encoding="utf-8")
    _restrict(pending, members_can_read=False)
    pending.replace(path)
    _restrict(path, members_can_read=False)


def service_vpn_credentials():
    """Return SessionCredentials only to the background service process."""
    from linkvideo_vpn_helper.services.vpn_service import SessionCredentials
    path = config_file().parent / "vpn_service.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SessionCredentials(
            _unprotect_machine(str(data["username_machine"])),
            _unprotect_machine(str(data["password_machine"])),
        )
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise RuntimeError("MikroTik для Telegram-бота ещё не настроен.") from exc


def service_vpn_configured() -> bool:
    return (config_file().parent / "vpn_service.json").is_file()


def clear_service_vpn_credentials():
    (config_file().parent / "vpn_service.json").unlink(missing_ok=True)


def _save_public_link(link: str):
    url = clean_bot_link(link)
    _ensure_folders()
    public_file().write_text(
        json.dumps({"bot_link": url}, ensure_ascii=False), "utf-8"
    )
    _restrict(public_file(), members_can_read=True)


def _task_command():
    executable = Path(sys.executable).resolve()
    # A privileged startup task may only start the installer-managed binary
    # under Program Files, never a user-writable portable copy.
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Установка фоновой задачи доступна только из EXE Helper.")
    root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")).resolve()
    expected = root / "LinkVideo.Helper" / "LinkVideo.Helper.exe"
    if executable != expected.resolve():
        raise RuntimeError("Устанавливать бота можно только из Program Files.")
    return str(executable)


def _startup_task_xml(executable: str) -> str:
    """Long-running LocalSystem task, no 72-hour limit, restart on failure."""
    if not executable or not Path(executable).is_absolute():
        raise ValueError("Укажите абсолютный путь к программе.")
    exe = xml_escape(executable)
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
        '<RegistrationInfo><Description>LinkVideo Telegram background worker</Description>'
        '</RegistrationInfo>'
        '<Triggers><BootTrigger><Enabled>true</Enabled></BootTrigger></Triggers>'
        '<Principals><Principal id="BotSystem">'
        '<UserId>S-1-5-18</UserId><LogonType>ServiceAccount</LogonType>'
        '<RunLevel>HighestAvailable</RunLevel></Principal></Principals>'
        '<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>'
        '<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>'
        '<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>'
        '<StartWhenAvailable>true</StartWhenAvailable>'
        '<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>'
        '<RestartOnFailure><Interval>PT1M</Interval><Count>50</Count></RestartOnFailure>'
        '<Enabled>true</Enabled></Settings>'
        '<Actions Context="BotSystem"><Exec>'
        f'<Command>{exe}</Command><Arguments>--telegram-worker</Arguments>'
        '</Exec></Actions></Task>'
    )


def install_startup_task():
    if os.name != "nt":
        raise RuntimeError("Автозапуск доступен только в Windows.")
    executable = _task_command()
    # No secrets or B2O login details in the task XML or command line.
    with tempfile.TemporaryDirectory(prefix="lv-bot-task-") as temp_dir:
        xml_path = Path(temp_dir) / "worker.xml"
        xml_path.write_bytes(("\ufeff" + _startup_task_xml(executable)).encode("utf-16-le"))
        result = _run_hidden([
            "schtasks.exe", "/Create", "/F", "/TN", TASK,
            "/XML", str(xml_path),
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
    try:
        return _run_hidden(["schtasks.exe", "/Query", "/TN", TASK], timeout=3).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


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
        elif operation == "b2o_configure":
            save_service_b2o_credentials(
                str(data.get("b2o_login") or ""),
                str(data.get("b2o_password") or ""),
            )
            response = "B2O подключён для автономного бота; учётные данные защищены Windows."
        elif operation == "b2o_clear":
            clear_service_b2o_credentials()
            response = "Автономная авторизация B2O удалена."
        elif operation == "vpn_configure":
            save_service_vpn_credentials(
                str(data.get("vpn_login") or ""),
                str(data.get("vpn_password") or ""),
            )
            response = "MikroTik для фонового бота сохранён защищённо."
        elif operation == "vpn_clear":
            clear_service_vpn_credentials()
            response = "Авторизация MikroTik для бота удалена."
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
        elif operation == "probe":
            response = probe_telegram_bot()
        elif operation == "stop":
            uninstall_startup_task()
            response = "Задача остановлена и удалена."
        else:
            raise ValueError("Неизвестная операция.")
        Path(str(data["result_path"])).write_text(
            json.dumps({"ok": True, "message": response}, ensure_ascii=False),
            "utf-8",
        )
    except Exception as exc:
        # Never return raw exceptions containing tokens, URLs, or passwords.
        category = (
            "B2O отклонил учётные данные или недоступен."
            if operation == "b2o_configure" else
            "Не удалось сохранить данные MikroTik."
            if operation == "vpn_configure" else
            "Ошибка прав Windows или проверки фоновой задачи."
            if operation in ("configure", "stop", "save_link") else
            "Не удалось изменить список доступа/проверить Telegram."
        )
        try:
            raw = json.loads(path.read_text("utf-8"))
            Path(str(raw["result_path"])).write_text(
                json.dumps({"ok": False, "message": category},
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
        # A standard user may authenticate UAC as a DIFFERENT admin account.
        # Grant local Administrators access to the private, random staging
        # folder and the existing request; keep the requester access so it
        # can read response.json. No permissions are granted to other users.
        for path, acl in ((Path(td), "*S-1-5-32-544:(OI)(CI)F"),
                          (request, "*S-1-5-32-544:F")):
            granted = _run_hidden(
                ["icacls.exe", str(path), "/grant", acl], timeout=8
            )
            if granted.returncode:
                raise RuntimeError("Не удалось подготовить защищённый запрос UAC.")
        # Powershell sees only a path; bot token is never supplied as a CLI arg.
        # Start-Process returns a process exit code after the elevated helper.
        # PowerShell Start-Process joins -ArgumentList strings internally:
        # quote the full command line, so paths with spaces cannot be split.
        argument_line = subprocess.list2cmdline(
            ["--telegram-admin-op", str(request)]
        ).replace("'", "''")
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "$p = Start-Process -FilePath '" +
            str(Path(sys.executable).resolve()).replace("'", "''") +
            "' -ArgumentList '" + argument_line +
            "' -Verb RunAs -PassThru -Wait; "
            "if ($null -eq $p) { exit 2 }; exit $p.ExitCode"
        )
        started = _run_hidden([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script,
        ], timeout=120)
        if started.returncode != 0 or not answer.exists():
            raise RuntimeError(
                "Повышенный процесс не запустился или не записал ответ. "
                "Проверьте UAC, антивирус и права на %TEMP%."
            )
        try:
            result = json.loads(answer.read_text("utf-8"))
            if not result.get("ok"):
                raise RuntimeError(str(result.get("message") or "Ошибка настройки."))
            return str(result.get("message") or "")
        finally:
            request.unlink(missing_ok=True)
