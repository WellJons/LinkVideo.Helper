from __future__ import annotations

"""Ula Oracle desktop connection: operator-supplied, never persisted secrets."""

import configparser
import os
import struct
import sys
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


def ula_radius_configured(config_dir: str | Path) -> bool:
    """Detect the external authentication selected by the original Ula client.

    Never parse passwords or other files: we only inspect the named Oracle
    Net setting that determines the required driver mode.
    """
    if not config_dir:
        return False
    path = Path(config_dir) / "sqlnet.ora"
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        # Only process the exact Oracle Net setting, never secrets or other
        # configuration fields. Comments cannot enable RADIUS.
        clean = line.split("#", 1)[0].split(";", 1)[0]
        normalized = "".join(clean.upper().split())
        if normalized == "SQLNET.AUTHENTICATION_SERVICES=(RADIUS)":
            return True
    return False


def _oci_architecture(dll: Path) -> str:
    """Read the PE machine field without loading or executing an untrusted DLL."""
    try:
        with dll.open("rb") as f:
            if f.read(2) != b"MZ":
                return "unknown"
            f.seek(0x3c)
            offset = struct.unpack("<I", f.read(4))[0]
            if not 64 <= offset <= 1024 * 1024:
                return "unknown"
            f.seek(offset)
            if f.read(4) != b"PE\x00\x00":
                return "unknown"
            machine = struct.unpack("<H", f.read(2))[0]
        return {0x8664: "x64", 0x014c: "x86", 0xaa64: "arm64"}.get(machine, "unknown")
    except (OSError, ValueError, struct.error):
        return "unknown"


def find_oracle_client(config_dir: str | Path = "") -> Path | None:
    """Locate an already installed 64-bit OCI; never search the whole drive.

    Search only the Ula install, configured Oracle paths and normal program
    locations. The PE header check avoids loading a 32-bit Ula OCI into the
    64-bit Helper. It does not guarantee that client dependencies exist.
    """
    seen: set[str] = set()
    candidates: list[Path] = []

    def add(folder):
        if folder:
            p = Path(folder).expanduser()
            key = os.path.normcase(str(p.absolute())).casefold()
            if key not in seen:
                seen.add(key)
                candidates.append(p)

    if config_dir:
        ula = Path(config_dir)
        add(ula)
        add(ula / "bin")
        add(ula.parent / "bin")
    add(Path(sys.executable).parent)
    if getattr(sys, "_MEIPASS", None):
        add(sys._MEIPASS)
    home = os.environ.get("ORACLE_HOME")
    if home:
        add(Path(home) / "bin")
        add(home)
    for part in os.environ.get("PATH", "").split(os.pathsep):
        add(part)

    # Probe a single directory level only; avoid a recursive filesystem scan.
    for env_name in ("ProgramFiles", "ProgramW6432"):
        root = os.environ.get(env_name)
        if not root:
            continue
        for base in (Path(root) / "Oracle", Path(root) / "oracle"):
            add(base)
            try:
                for child in sorted(base.iterdir()):
                    if child.is_dir():
                        add(child)
                        add(child / "bin")
            except OSError:
                # Optional Oracle installation directory may be inaccessible.
                # Continue with other explicitly identified search locations.
                continue

    for folder in candidates:
        dll = folder / "oci.dll"
        if dll.is_file() and _oci_architecture(dll) == "x64":
            return folder.resolve()
    return None


def create_ula_gateway(
    details: UlaConnectionDetails, password: str, *,
    thick_client_dir: str = "", config_dir: str = "",
):
    """Create a gateway with lazy driver/connection; does not connect yet.

    Password stays in memory only; callers must discard this gateway after use.
    For RADIUS, use the operator's existing Ula.sqlnet.ora in OCI Thick
    mode; Thin deliberately cannot authenticate through RADIUS.
    """
    details = details.validated()
    if not isinstance(password, str) or not password:
        raise ValueError("Введите пароль учётной записи Ula.")
    from linkvideo_vpn_helper.services.ula_oracle_gateway import UlaReadError
    radius = ula_radius_configured(config_dir)
    # A Ula installation often contains a 32-bit OCI. Never try to load that
    # DLL into the 64-bit Helper. Prefer an existing compatible installation.
    lib_dir = Path(thick_client_dir) if thick_client_dir else None
    if lib_dir is not None:
        dll = lib_dir / "oci.dll"
        if not dll.is_file():
            raise UlaReadError("Выбранная папка не содержит oci.dll.")
        if _oci_architecture(dll) != "x64":
            raise UlaReadError(
                "Выбрана несовместимая oci.dll (нужен x64). "
                "32-битную библиотеку установленной Ula загрузить в Helper нельзя."
            )
    elif radius:
        lib_dir = find_oracle_client(config_dir)
        if lib_dir is None:
            raise UlaReadError(
                "Ula использует RADIUS, но подходящий Oracle Client x64 на этом ПК "
                "не обнаружен. 32-битный клиент Ula несовместим с Helper x64. "
                "Проверьте установленный клиент; при наличии выберите его в дополнительных настройках."
            )
    try:
        import oracledb
    except ImportError as exc:
        raise RuntimeError("В сборке отсутствует python-oracledb.") from exc

    if lib_dir is not None:
        opts = {"lib_dir": str(lib_dir.resolve())}
        if config_dir:
            opts["config_dir"] = str(Path(config_dir).resolve())
        try:
            oracledb.init_oracle_client(**opts)
        except Exception:
            # Client initialization is process-global; changing OCI libraries
            # after initialization requires restarting the application.
            raise

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
