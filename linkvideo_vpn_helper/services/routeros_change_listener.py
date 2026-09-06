from __future__ import annotations

"""Event-driven RouterOS change stream for the temporary Sheets backend.

The old implementation re-read every VPN server every five minutes even when
nothing changed. RouterOS API exposes ``listen`` on printable menus and emits
``!re`` only when an item changes. Keep one lightweight API connection per
server, subscribe to the VPN-related menus and debounce the resulting events in
the existing Sheets coordinator.

A listener is deliberately only a trigger. RouterOS remains authoritative and
the normal sync service performs the verified snapshot after an event. If a
particular RouterOS menu does not support ``listen`` the other subscriptions
continue to work and manual full reconciliation remains available.
"""

import socket
import threading
import time
from collections.abc import Callable

from linkvideo_vpn_helper.mikrotik.api_ssl_client import RouterOSAPIClient
from linkvideo_vpn_helper.services.app_logging import event


WATCH_PATHS = (
    "/ppp/active",
    "/ppp/secret",
    "/ppp/profile",
    "/ip/firewall/nat",
)


def _sentence_tag(sentence: list[str]) -> str:
    for word in sentence[1:]:
        if word.startswith(".tag="):
            return word.split("=", 1)[1]
        if word.startswith("=.tag="):
            return word.split("=", 2)[2]
    return ""


def _sentence_values(sentence: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for word in sentence[1:]:
        if not word.startswith("="):
            continue
        raw = word[1:]
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key] = value
    return values


class RouterOSChangeListener:
    """Maintain one reconnecting RouterOS API listen session for one server."""

    def __init__(
        self,
        server: str,
        credentials,
        callback: Callable[[str, str, dict[str, str]], None],
    ) -> None:
        self.server = str(server or "").strip()
        self.credentials = credentials
        self.callback = callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client_lock = threading.Lock()
        self._client: RouterOSAPIClient | None = None

    def start(self) -> None:
        if not self.server or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"routeros-listen:{self.server}",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._client_lock:
            client = self._client
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def _run(self) -> None:
        delay = 2.0
        while not self._stop.is_set():
            try:
                self._listen_once()
                delay = 2.0
            except Exception as exc:
                if self._stop.is_set():
                    return
                event(
                    "LV",
                    "Поток событий RouterOS переподключается",
                    f"{self.server} · {str(exc)[:180]}",
                    level=30,
                )
                self._stop.wait(delay)
                delay = min(30.0, delay * 1.8)

    def _listen_once(self) -> None:
        creds = self.credentials
        client = RouterOSAPIClient(
            self.server,
            creds.username,
            creds.password,
            port=creds.port,
            timeout=max(4.0, float(creds.timeout or 4.5)),
        )
        client.connect()
        with self._client_lock:
            self._client = client

        try:
            # ``listen`` does not terminate. Tags let one API connection carry
            # several independent menu subscriptions at once.
            tag_to_path: dict[str, str] = {}
            for index, path in enumerate(WATCH_PATHS, start=1):
                tag = f"lv{index}"
                tag_to_path[tag] = path
                client._write_sentence([f"{path}/listen", f".tag={tag}"])

            # After login the socket timeout is useful for ordinary request/
            # response calls but wrong for an event stream that may legitimately
            # be quiet for hours. A blocking read is interrupted by stop() closing
            # the socket from another thread.
            if client.sock is not None:
                client.sock.settimeout(None)

            unsupported: set[str] = set()
            while not self._stop.is_set():
                sentence = client._read_sentence()
                if not sentence:
                    continue
                reply_type = sentence[0]
                tag = _sentence_tag(sentence)
                path = tag_to_path.get(tag, "")

                if reply_type == "!trap":
                    if path and path not in unsupported:
                        unsupported.add(path)
                        detail = _sentence_values(sentence).get("message", "listen не поддержан")
                        event(
                            "LV",
                            "RouterOS listen недоступен для меню",
                            f"{self.server} · {path} · {detail}",
                            level=30,
                        )
                    continue
                if reply_type != "!re" or not path:
                    continue

                values = _sentence_values(sentence)
                try:
                    self.callback(self.server, path, values)
                except Exception:
                    # A UI/database callback must never break the long-lived
                    # RouterOS event connection.
                    continue
        except (OSError, socket.error):
            if not self._stop.is_set():
                raise
        finally:
            with self._client_lock:
                if self._client is client:
                    self._client = None
            client.close()
