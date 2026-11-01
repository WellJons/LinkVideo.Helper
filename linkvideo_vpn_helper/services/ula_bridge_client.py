from __future__ import annotations

"""Local-only RPC to a packaged x86 OCI bridge, never to a remote endpoint."""

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from linkvideo_vpn_helper.services.ula_oracle_gateway import (
    UlaClientHit, UlaReadError, UlaService,
)


def find_bridge_exe() -> Path | None:
    # Only run the file installed beside our own signed/verified application.
    # Do not load arbitrary binaries from PATH or the current working directory.
    root = Path(sys.executable).resolve().parent
    bridge = root / "LinkVideo.UlaBridge_x86.exe"
    if bridge.is_file():
        return bridge
    return None


@dataclass(frozen=True)
class BridgeConnectionResult:
    def close(self) -> None:
        return None


class UlaX86BridgeGateway:
    def __init__(self, *, bridge: Path, client_dir: Path, config_dir: Path,
                 host: str, port: int, sid: str, username: str, password: str):
        self.bridge = bridge.resolve()
        self.client_dir = client_dir.resolve()
        self.config_dir = config_dir.resolve()
        self.host, self.port, self.sid = host, port, sid
        self.username, self.password = username, password

    def _invoke(self, action: str, **parameters):
        request = {
            "protocol": 1,
            "action": action,
            "lib_dir": str(self.client_dir),
            "config_dir": str(self.config_dir),
            "host": self.host,
            "port": self.port,
            "sid": self.sid,
            "username": self.username,
            "password": self.password,
            **parameters,
        }
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            response = subprocess.run(
                [str(self.bridge)], input=json.dumps(request, ensure_ascii=False),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", timeout=35,
                creationflags=flags, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise UlaReadError(
                "Превышено время ожидания ответа локального Oracle x86-посредника."
            ) from exc
        except OSError as exc:
            raise UlaReadError(
                "Не удалось запустить встроенный Oracle x86-посредник."
            ) from exc
        if response.returncode != 0 or len(response.stdout) > 8 * 1024 * 1024:
            raise UlaReadError("Oracle x86-посредник завершился с ошибкой.")
        try:
            data = json.loads(response.stdout)
        except (ValueError, TypeError) as exc:
            raise UlaReadError("Oracle x86-посредник вернул некорректный ответ.") from exc
        if not isinstance(data, dict) or not data.get("ok"):
            code = data.get("error") if isinstance(data, dict) else None
            if code == "ORA-01017":
                raise UlaReadError(
                    "ORA-01017: Oracle/RADIUS отклонил учётную запись. "
                    "Авторизация Ula может использовать отдельный прикладной вход."
                )
            if code in ("DPI-1047", "DPI-1072"):
                raise UlaReadError(
                    str(code) + ": существующий Oracle Client x86 несовместим "
                    "с используемым драйвером. Проверьте версию OCI."
                )
            if code == "OCI_ARCH":
                raise UlaReadError("Обнаружена неверная разрядность Oracle Client.")
            if code == "ORACLE_NET_CONFIG":
                raise UlaReadError("В каталоге Ula не найден sqlnet.ora.")
            if code == "BRIDGE_ARCH":
                raise UlaReadError("Oracle-посредник собран не для x86.")
            raise UlaReadError(
                f"Oracle x86-посредник: {code}"
                if isinstance(code, str) and code.isascii() and len(code) < 30
                else "Oracle x86-посредник не смог выполнить запрос."
            )
        return data.get("result")

    def _connect(self):
        if self._invoke("connect") is not True:
            raise UlaReadError("Oracle не подтвердил соединение.")
        return BridgeConnectionResult()

    def search_clients(self, search_text: str, *, search_type: int = 0):
        query = str(search_text or "").strip()
        if not 2 <= len(query) <= 100:
            raise UlaReadError("Введите от двух до 100 символов для поиска Ula.")
        if type(search_type) is not int or search_type not in (0, 1, 2):
            raise UlaReadError("Недопустимый режим поиска.")
        rows = self._invoke("find", search_text=query, search_type=search_type)
        if not isinstance(rows, list) or len(rows) > 200:
            raise UlaReadError("Некорректное число результатов поиска Ula.")
        try:
            return tuple(UlaClientHit(**row) for row in rows)
        except (TypeError, ValueError) as exc:
            raise UlaReadError("Некорректный формат результата поиска Ula.") from exc

    def list_services(self, client_id, *, include_non_active: bool, group_client: bool):
        ident = str(client_id).strip()
        if not ident.isascii() or not ident.isdecimal() or int(ident) <= 0:
            raise UlaReadError("Некорректный ID клиента Ula.")
        if type(include_non_active) is not bool or type(group_client) is not bool:
            raise UlaReadError("Некорректные параметры списка услуг.")
        rows = self._invoke(
            "services", client_id=ident,
            include_non_active=include_non_active,
            group_client=group_client,
        )
        if not isinstance(rows, list) or len(rows) > 3000:
            raise UlaReadError("Некорректное число услуг Ula.")
        try:
            return tuple(UlaService(**row) for row in rows)
        except (TypeError, ValueError) as exc:
            raise UlaReadError("Некорректный формат списка услуг Ula.") from exc
