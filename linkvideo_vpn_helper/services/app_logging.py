from __future__ import annotations

"""Persistent, privacy-conscious runtime log for LinkVideo.Helper.

The log is intended for support diagnostics, not packet-level tracing. It records
high-level application actions and failures while deliberately avoiding passwords,
Google private keys and authentication tokens.
"""

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any, Callable


LOGGER_NAME = "linkvideo.helper"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5
_LOCK = threading.RLock()
_FILE_HANDLER: RotatingFileHandler | None = None
_INSTALLED = False
_SERVICE_PATCHED = False
_AUTOMATION_PATCHED = False


_SECRET_PATTERNS = (
    re.compile(r"(?i)(password\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(private[_ -]?key\s*[=:]\s*)[^\n]+"),
    re.compile(r"(?i)(access[_ -]?token\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(refresh[_ -]?token\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"),
)


def log_dir() -> Path:
    base = str(os.getenv("LOCALAPPDATA", "") or "").strip()
    root = Path(base) if base else (Path.home() / "AppData" / "Local")
    return root / "LinkVideo.Helper" / "Logs"


def log_file() -> Path:
    return log_dir() / "LinkVideo.Helper.log"


def _redact(value: Any) -> str:
    text = str(value or "")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: match.group(1) + "***", text)
    # Never persist PEM blocks even if an exception accidentally contains one.
    text = re.sub(
        r"-----BEGIN [^-]+-----.*?-----END [^-]+-----",
        "<redacted private material>",
        text,
        flags=re.DOTALL,
    )
    return text


def _new_handler() -> RotatingFileHandler:
    folder = log_dir()
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_file(),
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
    return handler


def install_runtime_logging() -> logging.Logger:
    global _INSTALLED, _FILE_HANDLER
    with _LOCK:
        logger = logging.getLogger(LOGGER_NAME)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if _FILE_HANDLER is None:
            _FILE_HANDLER = _new_handler()
            logger.addHandler(_FILE_HANDLER)
        if not _INSTALLED:
            previous_hook = sys.excepthook

            def hook(exc_type, exc_value, exc_traceback):
                try:
                    logger.error(
                        "APP | Необработанное исключение | %s",
                        _redact(f"{getattr(exc_type, '__name__', exc_type)}: {exc_value}"),
                        exc_info=(exc_type, exc_value, exc_traceback),
                    )
                except Exception:
                    pass
                previous_hook(exc_type, exc_value, exc_traceback)

            sys.excepthook = hook
            _INSTALLED = True
        return logger


def event(area: str, action: str, detail: Any = "", *, level: int = logging.INFO) -> None:
    logger = install_runtime_logging()
    area_text = _redact(area).strip() or "APP"
    action_text = _redact(action).strip()
    detail_text = _redact(detail).strip()
    message = f"{area_text} | {action_text}"
    if detail_text:
        message += f" | {detail_text}"
    logger.log(level, message)


def error(area: str, action: str, exc: BaseException) -> None:
    event(area, action, f"{type(exc).__name__}: {exc}", level=logging.ERROR)


def read_recent(max_lines: int = 800) -> str:
    path = log_file()
    if not path.is_file():
        return "Журнал пока пуст."
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        return f"Не удалось прочитать журнал: {exc}"
    return "\n".join(lines[-max(50, int(max_lines)):]) if lines else "Журнал пока пуст."


def clear_logs() -> None:
    global _FILE_HANDLER
    with _LOCK:
        logger = logging.getLogger(LOGGER_NAME)
        if _FILE_HANDLER is not None:
            try:
                logger.removeHandler(_FILE_HANDLER)
                _FILE_HANDLER.close()
            except Exception:
                pass
            _FILE_HANDLER = None
        folder = log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        for candidate in folder.glob("LinkVideo.Helper.log*"):
            try:
                candidate.unlink()
            except Exception:
                pass
        _FILE_HANDLER = _new_handler()
        logger.addHandler(_FILE_HANDLER)
        event("APP", "Журнал очищен")


def _extract_server(args: tuple, kwargs: dict) -> str:
    if args:
        return str(args[0] or "").strip()
    return str(kwargs.get("server", "") or "").strip()


def _extract_login(method_name: str, args: tuple, kwargs: dict, result: Any = None) -> str:
    if method_name == "create_clients_batch":
        if result:
            values = [str(getattr(item, "login", "") or "").strip() for item in list(result or [])]
            return ", ".join(value for value in values if value)
        if len(args) >= 3:
            return str(args[2] or "").strip()
    if len(args) >= 3:
        return str(args[2] or "").strip()
    return str(kwargs.get("login", "") or "").strip()


def _wrap_method(cls, method_name: str, area: str, action: str) -> None:
    original = getattr(cls, method_name, None)
    if not callable(original) or getattr(original, "_linkvideo_runtime_logged", False):
        return

    def wrapper(self, *args, **kwargs):
        server = _extract_server(args, kwargs)
        login_before = _extract_login(method_name, args, kwargs)
        target = " · ".join(value for value in (server, login_before) if value)
        event(area, f"Начало: {action}", target)
        try:
            result = original(self, *args, **kwargs)
        except Exception as exc:
            error(area, f"Ошибка: {action}" + (f" · {target}" if target else ""), exc)
            raise
        login_after = _extract_login(method_name, args, kwargs, result)
        target_after = " · ".join(value for value in (server, login_after) if value)
        event(area, f"Готово: {action}", target_after)
        return result

    wrapper.__name__ = getattr(original, "__name__", method_name)
    wrapper.__doc__ = getattr(original, "__doc__", None)
    wrapper._linkvideo_runtime_logged = True
    setattr(cls, method_name, wrapper)


def install_operation_logging() -> None:
    """Log high-value user operations without logging read-only background polling."""
    global _SERVICE_PATCHED, _AUTOMATION_PATCHED
    install_runtime_logging()

    if not _SERVICE_PATCHED:
        try:
            from linkvideo_vpn_helper.services.vpn_service import VPNService
            operations = {
                "create_clients_batch": "Создание VPN-клиента",
                "add_ports": "Добавление NAT-портов",
                "remove_port": "Удаление NAT-порта",
                "set_password": "Смена PPP-пароля",
                "set_secret_enabled": "Изменение состояния PPP Secret",
                "set_port_enabled": "Изменение состояния NAT-порта",
                "recreate_port": "Пересоздание NAT-порта",
                "delete_client": "Удаление VPN-клиента",
                "disconnect_client_session": "Переподключение VPN",
            }
            for name, title in operations.items():
                _wrap_method(VPNService, name, "VPN", title)
            _SERVICE_PATCHED = True
        except Exception as exc:
            error("LOG", "Не удалось включить журнал VPN-операций", exc)

    if not _AUTOMATION_PATCHED:
        try:
            from linkvideo_vpn_helper.services.vpn_automation_service import VPNAutomationService
            operations = {
                "install_or_update": "Установка/обновление LV Automation",
                "set_automation_enabled": "Запуск/остановка LV Automation",
                "set_quarantine_enabled": "Изменение карантина LV",
                "seed_lifecycle": "Инициализация активности",
                "mark_manual_state": "Ручное состояние lifecycle",
            }
            for name, title in operations.items():
                _wrap_method(VPNAutomationService, name, "LV", title)
            _AUTOMATION_PATCHED = True
        except Exception as exc:
            error("LOG", "Не удалось включить журнал LV Automation", exc)
