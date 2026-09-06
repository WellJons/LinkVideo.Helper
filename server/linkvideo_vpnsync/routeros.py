from __future__ import annotations

import hashlib
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


class RouterOSError(RuntimeError):
    pass


class RouterOSClient:
    """Small RouterOS API client with 8728/8729 fallback.

    It intentionally has no desktop/Qt dependency so VPNSync can run as a small
    Linux service. TLS certificate validation is disabled only for RouterOS
    api-ssl because the existing fleet uses device-local/self-signed certs.
    Network access must therefore stay on the trusted LinkVideo network/VPN.
    """

    _port_cache: dict[str, int] = {}

    def __init__(self, host: str, username: str, password: str, *, port: int = 8728, timeout: float = 6.0) -> None:
        self.host = str(host or "").strip()
        self.username = str(username or "")
        self.password = str(password or "")
        self.port = int(port)
        self.timeout = max(1.0, float(timeout))
        self.sock: socket.socket | None = None
        self.connected_port: int | None = None

    def __enter__(self) -> "RouterOSClient":
        self.connect()
        return self

    def __exit__(self, *_args) -> None:
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

    def connect(self) -> None:
        key = self.host.lower()
        configured = int(self.port)
        cached = self._port_cache.get(key)
        first = cached if cached in (8728, 8729) else configured
        ports = [first]
        if first == 8728:
            ports.append(8729)
        elif first == 8729:
            ports.append(8728)

        last_error: Exception | None = None
        for port in dict.fromkeys(ports):
            try:
                self.sock = self._open_socket(port)
                self.connected_port = port
                self.login()
                if port in (8728, 8729):
                    self._port_cache[key] = port
                return
            except Exception as exc:
                last_error = exc
                self.close()
        raise RouterOSError(f"{self.host}: RouterOS API connection failed: {last_error}")

    def close(self) -> None:
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def login(self) -> None:
        replies = self.talk("/login", {"name": self.username, "password": self.password}, raise_on_trap=False)
        trap = next((item for item in replies if item.get("!trap") == "!trap"), None)
        if trap:
            raise RouterOSError(trap.get("message") or "RouterOS authentication failed")
        challenge = next((str(item.get("ret") or "") for item in replies if item.get("ret")), "")
        if not challenge and any(item.get("!done") == "!done" for item in replies):
            return
        if not challenge:
            replies = self.talk("/login", raise_on_trap=False)
            challenge = next((str(item.get("ret") or "") for item in replies if item.get("ret")), "")
        if not challenge:
            raise RouterOSError("RouterOS did not confirm authentication")
        md5 = hashlib.md5()
        md5.update(b"\x00")
        md5.update(self.password.encode("utf-8"))
        md5.update(bytes.fromhex(challenge))
        response = "00" + md5.hexdigest()
        replies = self.talk("/login", {"name": self.username, "response": response}, raise_on_trap=False)
        trap = next((item for item in replies if item.get("!trap") == "!trap"), None)
        if trap or not any(item.get("!done") == "!done" for item in replies):
            raise RouterOSError((trap or {}).get("message") or "RouterOS authentication failed")

    def talk(self, command: str, params: dict[str, Any] | None = None, *, raise_on_trap: bool = True) -> list[dict[str, str]]:
        if not self.sock:
            raise RouterOSError("RouterOS API is not connected")
        words = [command]
        for key, value in (params or {}).items():
            if value is None:
                continue
            if key.startswith("=") or key.startswith("?"):
                words.append(f"{key}{value}")
            else:
                words.append(f"={key}={value}")
        self._write_sentence(words)
        replies: list[dict[str, str]] = []
        while True:
            sentence = self._read_sentence()
            if not sentence:
                continue
            reply_type = sentence[0]
            parsed = self._parse_sentence(sentence)
            parsed[reply_type] = reply_type
            if reply_type == "!trap" and raise_on_trap:
                raise RouterOSError(parsed.get("message") or "RouterOS API trap")
            if reply_type == "!fatal":
                raise RouterOSError(parsed.get("message") or "RouterOS API fatal")
            replies.append(parsed)
            if reply_type == "!done":
                return replies

    def print(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, str]]:
        return [row for row in self.talk(f"{path}/print", params) if row.get("!re") == "!re"]

    def set(self, path: str, item_id: str, params: dict[str, Any]) -> None:
        self.talk(f"{path}/set", {**params, ".id": item_id})

    def disable(self, path: str, item_id: str) -> None:
        self.talk(f"{path}/disable", {".id": item_id})

    def enable(self, path: str, item_id: str) -> None:
        self.talk(f"{path}/enable", {".id": item_id})

    def remove(self, path: str, item_id: str) -> None:
        self.talk(f"{path}/remove", {".id": item_id})

    @staticmethod
    def _parse_sentence(sentence: list[str]) -> dict[str, str]:
        parsed: dict[str, str] = {}
        for word in sentence[1:]:
            if not word.startswith("="):
                continue
            raw = word[1:]
            if "=" in raw:
                key, value = raw.split("=", 1)
                parsed[key] = value
        return parsed

    def _write_sentence(self, words: list[str]) -> None:
        for word in words:
            self._write_word(word)
        self._write_word("")

    def _write_word(self, word: str) -> None:
        if self.sock is None:
            raise RouterOSError("RouterOS API is not connected")
        data = word.encode("utf-8")
        self.sock.sendall(self._encode_length(len(data)) + data)

    def _read_sentence(self) -> list[str]:
        result: list[str] = []
        while True:
            word = self._read_word()
            if word == "":
                return result
            result.append(word)

    def _read_word(self) -> str:
        length = self._read_length()
        if length == 0:
            return ""
        return self._recv_exact(length).decode("utf-8", errors="replace")

    def _recv_exact(self, length: int) -> bytes:
        if self.sock is None:
            raise RouterOSError("RouterOS API is not connected")
        chunks: list[bytes] = []
        remaining = length
        while remaining:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise RouterOSError("RouterOS API connection closed")
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

    @staticmethod
    def _encode_length(length: int) -> bytes:
        if length < 0x80:
            return bytes([length])
        if length < 0x4000:
            value = length | 0x8000
            return bytes([(value >> 8) & 0xFF, value & 0xFF])
        if length < 0x200000:
            value = length | 0xC00000
            return bytes([(value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF])
        if length < 0x10000000:
            value = length | 0xE0000000
            return bytes([(value >> 24) & 0xFF, (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF])
        return bytes([0xF0, (length >> 24) & 0xFF, (length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF])


@dataclass(slots=True, frozen=True)
class RouterTarget:
    host: str
    country: str
    username: str
    password: str
    port: int = 8728
    timeout: float = 6.0


WATCH_PATHS = ("/ppp/active", "/ppp/secret", "/ppp/profile", "/ip/firewall/nat")


def _sentence_tag(sentence: list[str]) -> str:
    for word in sentence[1:]:
        if word.startswith(".tag="):
            return word.split("=", 1)[1]
        if word.startswith("=.tag="):
            return word.split("=", 2)[2]
    return ""


def _sentence_values(sentence: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for word in sentence[1:]:
        if not word.startswith("="):
            continue
        raw = word[1:]
        if "=" in raw:
            key, value = raw.split("=", 1)
            result[key] = value
    return result


class RouterOSListener:
    """Reconnect forever and emit only real RouterOS list changes."""

    def __init__(self, target: RouterTarget, callback: Callable[[RouterTarget, str, dict[str, str]], None]) -> None:
        self.target = target
        self.callback = callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client_lock = threading.Lock()
        self._client: RouterOSClient | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"vpnsync-listen:{self.target.host}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._client_lock:
            client = self._client
        if client:
            client.close()

    def _run(self) -> None:
        delay = 2.0
        while not self._stop.is_set():
            try:
                self._listen_once()
                delay = 2.0
            except Exception as exc:
                if self._stop.is_set():
                    return
                print(f"[ROUTEROS] {self.target.host}: listener reconnect: {exc}", flush=True)
                self._stop.wait(delay)
                delay = min(30.0, delay * 1.8)

    def _listen_once(self) -> None:
        client = RouterOSClient(
            self.target.host,
            self.target.username,
            self.target.password,
            port=self.target.port,
            timeout=self.target.timeout,
        )
        client.connect()
        with self._client_lock:
            self._client = client
        try:
            tag_to_path: dict[str, str] = {}
            for index, path in enumerate(WATCH_PATHS, start=1):
                tag = f"lv{index}"
                tag_to_path[tag] = path
                client._write_sentence([f"{path}/listen", f".tag={tag}"])
            if client.sock is not None:
                client.sock.settimeout(None)
            while not self._stop.is_set():
                sentence = client._read_sentence()
                if not sentence:
                    continue
                reply_type = sentence[0]
                tag = _sentence_tag(sentence)
                path = tag_to_path.get(tag, "")
                if reply_type == "!trap":
                    detail = _sentence_values(sentence).get("message", "listen unsupported")
                    print(f"[ROUTEROS] {self.target.host}: {path} listen unavailable: {detail}", flush=True)
                    continue
                if reply_type == "!re" and path:
                    self.callback(self.target, path, _sentence_values(sentence))
        finally:
            with self._client_lock:
                if self._client is client:
                    self._client = None
            client.close()
