from __future__ import annotations

"""Ula Oracle desktop connection: operator-supplied, never persisted secrets."""

import configparser
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UlaConnectionDetails:
    host: str
    port: int
    sid: str
    username: str

    def validated(self) -> "UlaConnectionDetails":
        host = str(self.host).strip()
        sid = str(self.sid).strip()
        user = str(self.username).strip()
        port = int(self.port)
        if not host or len(host) > 253 or any(x.isspace() for x in host):
            raise ValueError("Укажите корректный адрес Oracle из Ula.ini.")
        if not 1 <= port <= 65535:
            raise ValueError("Порт Oracle должен быть от 1 до 65535.")
        if not sid or len(sid) > 128 or any(x.isspace() for x in sid):
            raise ValueError("Укажите SID Oracle из Ula.ini.")
        if not user or len(user) > 128:
            raise ValueError("Укажите имя пользователя Oracle.")
        return UlaConnectionDetails(host, port, sid, user)


def read_ula_ini(filename: str | Path) -> UlaConnectionDetails:
    path = Path(filename)
    if path.name.lower() != "ula.ini":
        raise ValueError("Выберите конфигурационный файл Ula.ini из папки Ula.")
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open("r", encoding="cp1251") as f:
            parser.read_file(f)
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise ValueError("Не удалось прочитать Ula.ini.") from exc
    if not parser.has_section("DB"):
        raise ValueError("В Ula.ini отсутствует раздел [DB].")
    try:
        result = UlaConnectionDetails(
            host=parser.get("DB", "Server"),
            port=parser.getint("DB", "Port"),
            sid=parser.get("DB", "Sid"),
            username=parser.get("DB", "UserName"),
        )
    except (ValueError, configparser.Error) as exc:
        raise ValueError("В Ula.ini не найдены Server, Port, Sid или UserName.") from exc
    return result.validated()


def create_ula_gateway(details: UlaConnectionDetails, password: str, *, thick_client_dir: str = ""):
    """Create a gateway with lazy driver/connection; does not connect yet.

    Password stays in memory only; callers must discard this gateway after use.
    64-bit Oracle Instant Client 19+ may be required for older DB servers.
    """
    details = details.validated()
    if not isinstance(password, str) or not password:
        raise ValueError("Укажите пароль Oracle для проверки подключения.")
    try:
        import oracledb
    except ImportError as exc:
        raise RuntimeError("В сборке отсутствует python-oracledb.") from exc

    if thick_client_dir:
        oracledb.init_oracle_client(lib_dir=thick_client_dir)

    dsn = oracledb.makedsn(details.host, details.port, sid=details.sid)

    def connect():
        connection = oracledb.connect(
            user=details.username, password=password, dsn=dsn,
            tcp_connect_timeout=8,
        )
        connection.call_timeout = 12000
        connection.autocommit = False
        return connection

    from linkvideo_vpn_helper.services.ula_oracle_gateway import UlaOracleReadOnlyGateway
    return UlaOracleReadOnlyGateway(connect, oracledb.DB_TYPE_CURSOR)
