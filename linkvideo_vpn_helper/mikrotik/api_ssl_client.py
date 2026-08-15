from __future__ import annotations

import hashlib
import socket
import ssl
from typing import Any, Dict, List, Optional


class RouterOSAPIError(Exception):
    pass


class RouterOSAPIClient:
    # Если у конкретного MikroTik включён только api-ssl, запоминаем найденный
    # стандартный API-порт на время текущего запуска Helper. Это особенно
    # полезно для новых региональных серверов: после первого успешного fallback
    # последующие запросы не делают лишнее соединение.
    _port_cache: dict[str, int] = {}

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 8728,
        timeout: float = 5.0,
    ) -> None:
        self.host = host.strip()
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.connected_port: int | None = None

    def __enter__(self) -> "RouterOSAPIClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _open_socket(self, port: int) -> socket.socket:
        raw = socket.create_connection((self.host, port), timeout=self.timeout)
        raw.settimeout(self.timeout)
        if port != 8729:
            return raw

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        try:
            wrapped = context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise
        wrapped.settimeout(self.timeout)
        return wrapped

    def _winbox_reachable(self, timeout: float = 1.2) -> bool:
        """Best-effort probe used only to explain API failures.

        WinBox on 8291 is not the RouterOS API, but if 8291 accepts TCP while
        both 8728/8729 refuse connections, Helper can tell the operator that
        credentials/network to the router are probably fine and the API service
        itself is disabled/filtered. No WinBox authentication is attempted.
        """
        try:
            probe = socket.create_connection((self.host, 8291), timeout=min(timeout, self.timeout))
            probe.close()
            return True
        except Exception:
            return False

    def connect(self) -> None:
        """Connect to RouterOS API with a narrow standard-port fallback.

        WinBox and RouterOS API are different services. Some regional routers
        can have plain API/8728 disabled while API-SSL/8729 is enabled. If the
        selected standard API port *actively refuses* the TCP connection, Helper
        immediately tries the other standard API port. Timeouts are not doubled:
        fallback is intentionally limited to a fast connection-refused case.
        """
        key = self.host.lower()
        configured = int(self.port)
        cached = self._port_cache.get(key)
        first_port = cached if cached in (8728, 8729) else configured
        ports = [first_port]
        if first_port == 8728:
            ports.append(8729)
        elif first_port == 8729:
            ports.append(8728)

        first_refused: ConnectionRefusedError | None = None
        for index, port in enumerate(dict.fromkeys(ports)):
            try:
                self.sock = self._open_socket(port)
                self.connected_port = port
                self.port = port
                self.login()
                if port in (8728, 8729):
                    self._port_cache[key] = port
                return
            except ConnectionRefusedError as exc:
                self.close()
                self.connected_port = None
                self._port_cache.pop(key, None)
                if first_refused is None:
                    first_refused = exc
                # Fallback only between the two standard RouterOS API ports,
                # and only for an immediate TCP refusal.
                if index + 1 < len(ports):
                    continue
                if self._winbox_reachable():
                    raise RouterOSAPIError(
                        "WinBox доступен, но RouterOS API 8728/8729 не принимает соединение. "
                        "Проверьте /ip service: api или api-ssl должен быть включён и разрешён firewall."
                    ) from exc
                raise first_refused
            except Exception:
                self.close()
                self.connected_port = None
                self._port_cache.pop(key, None)
                raise

        if first_refused is not None:
            raise first_refused

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def login(self) -> None:
        """Авторизация RouterOS с корректным разбором !trap/!done.

        Раньше !done после !trap ошибочно считался успешным входом. В результате
        неверный пароль мог проявляться только на следующей команде как !fatal
        ("not logged in"), что UI показывал как непонятную фатальную ошибку API.
        """
        replies = self.talk(
            "/login", {"name": self.username, "password": self.password},
            raise_on_trap=False,
        )
        trap = next((item for item in replies if item.get("!trap") == "!trap"), None)
        if trap:
            message = trap.get("message") or trap.get("category") or "Неверный логин или пароль RouterOS"
            raise RouterOSAPIError(message)

        # Старый challenge-response RouterOS мог вернуть ret даже на первый
        # запрос с credentials. Если ret нет и есть !done — новый login успешен.
        challenge = next((str(item.get("ret") or "") for item in replies if item.get("ret")), "")
        if not challenge and any(item.get("!done") == "!done" for item in replies):
            return

        if not challenge:
            replies = self.talk("/login", raise_on_trap=False)
            trap = next((item for item in replies if item.get("!trap") == "!trap"), None)
            if trap:
                message = trap.get("message") or "Не удалось выполнить авторизацию в RouterOS API"
                raise RouterOSAPIError(message)
            challenge = next((str(item.get("ret") or "") for item in replies if item.get("ret")), "")

        if not challenge:
            raise RouterOSAPIError("Не удалось выполнить авторизацию в RouterOS API")

        response = self._challenge_response(challenge, self.password)
        replies = self.talk(
            "/login", {"name": self.username, "response": response},
            raise_on_trap=False,
        )
        trap = next((item for item in replies if item.get("!trap") == "!trap"), None)
        if trap:
            message = trap.get("message") or "Неверный логин или пароль RouterOS"
            raise RouterOSAPIError(message)
        if not any(item.get("!done") == "!done" for item in replies):
            raise RouterOSAPIError("RouterOS не подтвердил авторизацию")

    def _challenge_response(self, challenge_hex: str, password: str) -> str:
        challenge = bytes.fromhex(challenge_hex)
        md5 = hashlib.md5()
        md5.update(b"\x00")
        md5.update(password.encode("utf-8"))
        md5.update(challenge)
        return "00" + md5.hexdigest()

    def talk(
        self,
        command: str,
        params: Optional[Dict[str, Any]] = None,
        raise_on_trap: bool = True,
    ) -> List[Dict[str, str]]:
        if not self.sock:
            raise RouterOSAPIError("Подключение к API не установлено")

        words = [command]
        if params:
            for key, value in params.items():
                if value is None:
                    continue
                if key.startswith("=") or key.startswith("?"):
                    words.append(f"{key}{value}")
                else:
                    words.append(f"={key}={value}")
        self._write_sentence(words)

        replies: List[Dict[str, str]] = []
        while True:
            sentence = self._read_sentence()
            if not sentence:
                continue
            reply_type = sentence[0]
            parsed = self._parse_sentence(sentence)
            parsed[reply_type] = reply_type

            if reply_type == "!trap" and raise_on_trap:
                message = parsed.get("message") or parsed.get("category") or "Неизвестная ошибка API"
                raise RouterOSAPIError(message)
            if reply_type == "!fatal":
                message = parsed.get("message") or "Фатальная ошибка API"
                raise RouterOSAPIError(message)
            replies.append(parsed)
            if reply_type == "!done":
                break
        return replies

    def add(self, path: str, params: Dict[str, Any]) -> str:
        replies = self.talk(f"{path}/add", params)
        for item in replies:
            if "ret" in item:
                return item["ret"]
        return ""

    def remove(self, path: str, item_id: str) -> None:
        self.talk(f"{path}/remove", {".id": item_id})

    def set(self, path: str, item_id: str, params: Dict[str, Any]) -> None:
        params = {**params, ".id": item_id}
        self.talk(f"{path}/set", params)

    def enable(self, path: str, item_id: str) -> None:
        self.talk(f"{path}/enable", {".id": item_id})

    def disable(self, path: str, item_id: str) -> None:
        self.talk(f"{path}/disable", {".id": item_id})

    def print(self, path: str, extra_params: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
        replies = self.talk(f"{path}/print", extra_params)
        return [item for item in replies if item.get("!re") == "!re"]

    def _parse_sentence(self, sentence: List[str]) -> Dict[str, str]:
        parsed: Dict[str, str] = {}
        for word in sentence[1:]:
            if not word:
                continue
            if word.startswith("="):
                stripped = word[1:]
                if "=" in stripped:
                    key, value = stripped.split("=", 1)
                    parsed[key] = value
        return parsed

    def _write_sentence(self, words: List[str]) -> None:
        for word in words:
            self._write_word(word)
        self._write_word("")

    def _write_word(self, word: str) -> None:
        assert self.sock is not None
        data = word.encode("utf-8")
        self.sock.sendall(self._encode_length(len(data)) + data)

    def _read_sentence(self) -> List[str]:
        sentence: List[str] = []
        while True:
            word = self._read_word()
            if word == "":
                break
            sentence.append(word)
        return sentence

    def _read_word(self) -> str:
        assert self.sock is not None
        length = self._read_length()
        if length == 0:
            return ""
        data = self._recv_exact(length)
        return data.decode("utf-8", errors="replace")

    def _recv_exact(self, length: int) -> bytes:
        assert self.sock is not None
        chunks = []
        remaining = length
        while remaining > 0:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise RouterOSAPIError("Соединение с API было закрыто")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_length(self) -> int:
        first = self._recv_exact(1)[0]
        if (first & 0x80) == 0x00:
            return first
        if (first & 0xC0) == 0x80:
            second = self._recv_exact(1)[0]
            return ((first & ~0xC0) << 8) + second
        if (first & 0xE0) == 0xC0:
            rest = self._recv_exact(2)
            return ((first & ~0xE0) << 16) + (rest[0] << 8) + rest[1]
        if (first & 0xF0) == 0xE0:
            rest = self._recv_exact(3)
            return ((first & ~0xF0) << 24) + (rest[0] << 16) + (rest[1] << 8) + rest[2]
        rest = self._recv_exact(4)
        return (rest[0] << 24) + (rest[1] << 16) + (rest[2] << 8) + rest[3]

    def _encode_length(self, length: int) -> bytes:
        if length < 0x80:
            return bytes([length])
        if length < 0x4000:
            length |= 0x8000
            return bytes([(length >> 8) & 0xFF, length & 0xFF])
        if length < 0x200000:
            length |= 0xC00000
            return bytes([(length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF])
        if length < 0x10000000:
            length |= 0xE0000000
            return bytes([
                (length >> 24) & 0xFF,
                (length >> 16) & 0xFF,
                (length >> 8) & 0xFF,
                length & 0xFF,
            ])
        return bytes([0xF0]) + bytes([
            (length >> 24) & 0xFF,
            (length >> 16) & 0xFF,
            (length >> 8) & 0xFF,
            length & 0xFF,
        ])
