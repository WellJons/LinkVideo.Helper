from __future__ import annotations

"""64-bit GUI to 32-bit Ula Oracle process boundary.

No Oracle client, employee credentials, or SQL strings are stored on disk.
Only built-in read-only operations are recognized by the x86 worker.
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from linkvideo_vpn_helper.services.ula_oracle_gateway import (
    UlaClientHit, UlaReadError, UlaService,
)


def find_bridge_exe() -> Path | None:
    base = Path(sys.executable).resolve().parent
    choices = (
        base / "LinkVideo.UlaBridge32.exe",
        base / "_internal" / "LinkVideo.UlaBridge32.exe",
    )
    for path in choices:
        if path.is_file():
            return path
    return None


def run_bridge(
    action: str, *, details, login: str, password: str,
    client_dir: Path, config_dir: str, **params,
) -> dict:
    bridge = find_bridge_exe()
    if bridge is None:
        raise UlaReadError(
            "В этой сборке отсутствует 32-битный модуль Oracle. "
            "Установите последнюю тестовую версию Helper RC."
        )
    request = {
        "action": action,
        "details": asdict(details),
        "login": login,
        "password": password,
        "client_dir": str(client_dir),
        "config_dir": str(config_dir),
    }
    request.update(params)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [str(bridge), "--stdio-once"],
            input=json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n",
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
            creationflags=flags,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        raise UlaReadError("Истекло время ожидания ответа Oracle x86.") from exc
    except OSError as exc:
        raise UlaReadError("Не удалось запустить 32-битный модуль Oracle.") from exc
    if result.returncode != 0:
        raise UlaReadError("32-битный модуль Oracle завершился с ошибкой запуска.")
    if len(result.stdout) > 2_000_000:
        raise UlaReadError("Ответ Oracle превышает допустимый размер.")
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise UlaReadError("Неправильный ответ 32-битного модуля Oracle.") from exc
    if not isinstance(payload, dict) or type(payload.get("ok")) is not bool:
        raise UlaReadError("Некорректный ответ модуля Oracle.")
    if not payload["ok"]:
        message = payload.get("error")
        code = re.fullmatch(r"(?:ORA|DPI)-\d{4,5}", str(message or ""))
        raise UlaReadError(code.group(0) if code else "Не удалось выполнить запрос Oracle x86.")
    if not isinstance(payload.get("rows"), list):
        raise UlaReadError("Некорректные данные в ответе Oracle x86.")
    return payload


class UlaX86BridgeGateway:
    """Gateway API expected by the existing Ula page, one worker per request."""

    def __init__(self, *, details, login: str, password: str, client_dir: Path, config_dir: str):
        self._details = details
        self._login = login
        self._password = password
        self._client_dir = client_dir
        self._config_dir = config_dir

    def _invoke(self, action, **params):
        return run_bridge(
            action, details=self._details, login=self._login,
            password=self._password, client_dir=self._client_dir,
            config_dir=self._config_dir, **params,
        )["rows"]

    def _connect(self):
        self._invoke("test")

        class ConnectionProbe:
            def close(self):
                return None

        return ConnectionProbe()

    def search_clients(self, search_text: str, *, search_type: int = 0):
        rows = self._invoke("find", query=search_text, search_type=search_type)
        if len(rows) > 200:
            raise UlaReadError("Слишком много результатов поиска Ula.")
        try:
            return tuple(UlaClientHit(**item) for item in rows)
        except (TypeError, ValueError) as exc:
            raise UlaReadError("Некорректный ответ поиска Ula.") from exc

    def list_services(self, client_id, *, include_non_active: bool, group_client: bool):
        rows = self._invoke(
            "services", client_id=str(client_id),
            include_non_active=include_non_active, group_client=group_client,
        )
        if len(rows) > 3000:
            raise UlaReadError("Слишком много услуг Ula.")
        try:
            return tuple(UlaService(**item) for item in rows)
        except (TypeError, ValueError) as exc:
            raise UlaReadError("Некорректный ответ услуг Ula.") from exc
