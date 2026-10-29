from __future__ import annotations

"""LD camera settings through the operator's authenticated agent-vcore proxy.

The API contract (camera/list, proxy-pipe, LAPI Time/Image/Video) is taken
from the operator-provided patch. No credentials are written to disk.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import re
import time
from urllib.parse import parse_qsl, urlsplit
from threading import Lock
from typing import Any

import requests


@dataclass(frozen=True)
class AgentCamera:
    serial_number: str
    server: str
    agent_version: tuple[int, ...] = ()
    vendor: str = ""
    model: str = ""
    connect_time: str = ""

    @property
    def version_text(self) -> str:
        return ".".join(map(str, self.agent_version)) if self.agent_version else "—"


class AgentService:
    """Camera discovery and camera settings. Only allow known agent-vcore hosts."""

    PORT = 19999
    SERVERS = (
        "agent-vcore01.video.goodline.info",
        "agent-vcore02.video.goodline.info",
        "agent-vcore03.video.goodline.info",
        "agent-vcore04.video.goodline.info",
        "agent-vcore05.video.goodline.info",
        "agent-vcore06.video.goodline.info",
        "agent-vcoreA.video.goodline.info",
        "rb-agent-vcore01.video.goodline.info",
        "rb-agent-test-vcore01.video.goodline.info",
        "b2o-vcore-agent-test.video.goodline.info",
    )
    IMAGE_ENHANCE_PATH = "/LAPI/V1.0/Channels/0/Image/Enhance"
    VIDEO_STREAMS_PATH = "/LAPI/V1.0/Channels/1/Media/Video/Streams/DetailInfos"
    _SERIAL = re.compile(r"[A-Za-z0-9_.-]{3,96}\Z")

    def __init__(self, settings=None, credentials=None, timeout: float = 3.0):
        self.settings = settings
        self.credentials = credentials
        self.timeout = float(timeout)
        self._cache: dict[str, str] = {}
        self._cache_lock = Lock()

    def servers(self) -> tuple[str, ...]:
        return self.SERVERS

    def _auth(self) -> tuple[str, str]:
        if self.credentials is None:
            raise PermissionError("Войдите в LinkVideo.Helper для работы с agent-vcore.")
        username = str(getattr(self.credentials, "username", "") or "")
        password = str(getattr(self.credentials, "password", "") or "")
        if not username or not password:
            raise PermissionError("Нет учётных данных оператора.")
        return username, password

    @classmethod
    def _url(cls, server: str, path: str) -> str:
        if server not in cls.SERVERS or not path.startswith("/api/v1/camera/"):
            raise ValueError("Недопустимый сервер или путь агента.")
        return f"http://{server}:{cls.PORT}{path}"

    @classmethod
    def _validated_camera(cls, camera: AgentCamera):
        if camera.server not in cls.SERVERS or not cls._SERIAL.fullmatch(camera.serial_number):
            raise ValueError("Неизвестный агент или некорректный serial_number.")

    @staticmethod
    def _serial_from_row(row: dict[str, Any]) -> str:
        """Support known naming variations without treating an arbitrary ID as a serial."""
        for key in ("serial_number", "serialNumber", "SerialNumber", "serial_no"):
            value = row.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    @staticmethod
    def _camera_rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            rows = []
            for item in payload:
                rows.extend(AgentService._camera_rows(item))
            return rows
        if not isinstance(payload, dict):
            return []
        if AgentService._serial_from_row(payload):
            return [payload]
        for name in ("cameras", "cameraList", "camera_list", "items", "records", "data", "result", "list"):
            item = payload.get(name)
            if isinstance(item, (list, dict)):
                rows = AgentService._camera_rows(item)
                if rows:
                    return rows
        return []

    def camera_list(self, server: str, target_serial: str = "") -> list[dict[str, Any]]:
        """Read all discoverable pages, never silently report an incomplete list as empty.

        The first request stays identical to the established /camera/list contract.
        Pagination is attempted only when the response advertises it or a
        suspiciously full (50+) page could have been truncated by the gateway.
        All subsequent requests remain on the explicitly allowlisted host/path.
        """
        url = self._url(server, "/api/v1/camera/list")
        rows_by_serial: dict[str, dict[str, Any]] = {}
        params: dict[str, Any] | None = None
        requested: set[tuple[tuple[str, str], ...]] = set()
        for _page in range(40):
            marker = tuple(sorted((str(k), str(v)) for k, v in (params or {}).items()))
            if marker in requested:
                raise ConnectionError(f"agent-vcore {server}: повтор страницы списка камер.")
            requested.add(marker)
            kwargs = {"headers": {"Accept": "application/json"},
                      "auth": self._auth(), "timeout": self.timeout,
                      "allow_redirects": False}
            if params:
                kwargs["params"] = params
            response = requests.get(url, **kwargs)
            response.raise_for_status()
            payload = response.json()
            page_rows = self._camera_rows(payload)
            if not isinstance(payload, (dict, list)):
                raise ValueError(f"agent-vcore {server}: некорректный формат списка камер.")
            if isinstance(payload, dict) and payload and not page_rows and not any(
                isinstance(payload.get(key), (list, dict))
                for key in ("cameras", "cameraList", "camera_list", "items",
                            "records", "data", "result", "list")
            ):
                raise ValueError(f"agent-vcore {server}: неизвестный формат ответа со списком камер.")
            new_count = 0
            for row in page_rows:
                serial = self._serial_from_row(row)
                if not serial:
                    continue
                key = serial.casefold()
                if key not in rows_by_serial:
                    new_count += 1
                rows_by_serial[key] = row
            if target_serial and target_serial.casefold() in rows_by_serial:
                return list(rows_by_serial.values())

            meta = payload if isinstance(payload, dict) else {}
            for name in ("pagination", "paging", "pageInfo", "meta"):
                if isinstance(meta.get(name), dict):
                    meta = {**meta, **meta[name]}
                    break
            next_page = meta.get("next") or meta.get("next_page") or meta.get("nextPage")
            has_more = meta.get("has_more", meta.get("hasMore"))
            raw_total = meta.get("total", meta.get("total_count", meta.get("totalCount")))
            try:
                total = int(raw_total) if raw_total is not None else None
            except (ValueError, TypeError):
                total = None

            if isinstance(next_page, str) and next_page:
                # Ignore remote URLs/paths supplied by an untrusted inventory response.
                parsed = urlsplit(next_page)
                if parsed.scheme or parsed.netloc or (parsed.path and parsed.path != "/api/v1/camera/list"):
                    raise ConnectionError(f"agent-vcore {server}: небезопасный переход страницы.")
                next_params = dict(parse_qsl(parsed.query, keep_blank_values=False))
                if not next_params:
                    raise ConnectionError(f"agent-vcore {server}: нет параметров следующей страницы.")
                params = next_params
            elif isinstance(next_page, int) and not isinstance(next_page, bool):
                params = {"page": next_page}
            elif has_more is True or (total is not None and len(rows_by_serial) < total):
                if not page_rows or not new_count:
                    raise ConnectionError(f"agent-vcore {server}: неполный список камер.")
                page_key = "page" if "page" in meta or "page" in (params or {}) else "offset"
                if page_key == "page":
                    params = {"page": int((params or {}).get("page", meta.get("page", 1))) + 1,
                              "page_size": int(meta.get("page_size", meta.get("limit", len(page_rows))))}
                else:
                    params = {"offset": len(rows_by_serial), "limit": int(meta.get("limit", len(page_rows)))}
            elif has_more is False or (total is not None and len(rows_by_serial) >= total):
                return list(rows_by_serial.values())
            elif len(page_rows) >= 50 and params is None:
                # A capped plain list may omit pagination metadata altogether.
                params = {"offset": len(page_rows), "limit": len(page_rows)}
            elif params is not None:
                if not page_rows:
                    return list(rows_by_serial.values())
                if not new_count:
                    raise ConnectionError(f"agent-vcore {server}: сервер игнорирует постраничный запрос.")
                limit = int(params.get("limit", params.get("page_size", len(page_rows))))
                if len(page_rows) < limit:
                    return list(rows_by_serial.values())
                if "page" in params:
                    params = {**params, "page": int(params["page"]) + 1}
                else:
                    params = {**params, "offset": len(rows_by_serial)}
            else:
                return list(rows_by_serial.values())
        raise ConnectionError(f"agent-vcore {server}: превышен предел страниц списка камер.")

    @staticmethod
    def _from_row(server: str, row: dict[str, Any]) -> AgentCamera:
        raw = row.get("agent_version") or []
        if not isinstance(raw, (tuple, list)):
            raw = []
        return AgentCamera(
            serial_number=AgentService._serial_from_row(row),
            server=server,
            agent_version=tuple(int(v) for v in raw if str(v).isdigit()),
            vendor=str(row.get("vendor") or "").strip(),
            model=str(row.get("model") or "").strip(),
            connect_time=str(row.get("connect_time") or "").strip(),
        )

    def _find_on_server(self, server: str, serial: str) -> AgentCamera | None:
        for row in self.camera_list(server, target_serial=serial):
            if self._serial_from_row(row).casefold() == serial.casefold():
                return self._from_row(server, row)
        return None

    def find_camera(self, serial_number: str) -> AgentCamera:
        serial = str(serial_number or "").strip()
        if not self._SERIAL.fullmatch(serial):
            raise ValueError("Введите корректный serial_number (латиница, цифры, ._-).")
        self._auth()  # Fail before spawning network requests when credentials absent.
        key = serial.casefold()
        with self._cache_lock:
            cached = self._cache.get(key)
        if cached:
            try:
                found = self._find_on_server(cached, serial)
                if found:
                    return found
            except requests.RequestException:
                # A stale cache must never block a full inventory search.
                found = None
            with self._cache_lock:
                self._cache.pop(key, None)
        others = [host for host in self.servers() if host != cached]
        failures = []
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(others)))) as pool:
            tasks = {pool.submit(self._find_on_server, host, serial): host for host in others}
            for future in as_completed(tasks):
                try:
                    found = future.result()
                except Exception:
                    failures.append(tasks[future])
                    continue
                if found:
                    self._validated_camera(found)
                    with self._cache_lock:
                        self._cache[key] = found.server
                    for pending in tasks:
                        pending.cancel()
                    return found
        if failures:
            raise ConnectionError(
                f"Поиск не завершён: {len(failures)} из {len(others)} agent-vcore "
                "не вернули полный список камер. Повторите проверку или проверьте доступность серверов: "
                + ", ".join(failures[:5]) + ("…" if len(failures) > 5 else "")
            )
        raise LookupError(f"Камера {serial} отсутствует в полученных списках agent-vcore.")

    @staticmethod
    def _raw_http_get(path: str) -> bytes:
        return (f"GET {path} HTTP/1.1\r\n"
                "Host: 127.0.0.1:80\r\n"
                "Accept: application/json\r\n\r\n").encode("ascii")

    @staticmethod
    def _raw_http_put(path: str, payload: dict[str, Any]) -> bytes:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        header = (f"PUT {path} HTTP/1.1\r\n"
                  "Host: 127.0.0.1:80\r\n"
                  "Content-Type: application/json\r\n"
                  f"Content-Length: {len(data)}\r\n\r\n").encode("ascii")
        return header + data

    def proxy_pipe(self, camera: AgentCamera, request_bytes: bytes, port: int = 80) -> requests.Response:
        self._validated_camera(camera)
        if port != 80:
            raise ValueError("Для настроек LD поддерживается только порт 80.")
        url = self._url(camera.server, f"/api/v1/camera/{camera.serial_number}/proxy-pipe")
        r = requests.put(url, params={"port": 80}, auth=self._auth(),
                         headers={"Accept": "application/json", "Content-Type": "application/json"},
                         data=request_bytes, timeout=max(10.0, self.timeout),
                         allow_redirects=False)
        r.raise_for_status()
        return r

    @staticmethod
    def _proxy_payload(response: requests.Response) -> dict[str, Any]:
        raw = response.content
        if b"\r\n\r\n" in raw:
            raw = raw.split(b"\r\n\r\n", 1)[1]
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise ValueError("LiteAPI вернула ответ не в формате JSON.") from error
        if not isinstance(payload, dict):
            raise ValueError("Неверный ответ LiteAPI.")
        envelope = payload.get("Response")
        if isinstance(envelope, dict):
            code = envelope.get("ResponseCode")
            if code not in (None, 0, "0"):
                raise RuntimeError(f"LiteAPI ResponseCode={code}: {envelope.get('ResponseString', '')}")
            data = envelope.get("Data")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    # Invalid embedded JSON is not a settings document.
                    data = None
            if isinstance(data, dict):
                return data
        return payload

    def _get_lapi_json(self, camera: AgentCamera, path: str) -> dict[str, Any]:
        return self._proxy_payload(self.proxy_pipe(camera, self._raw_http_get(path)))

    def configure_time(self, camera: AgentCamera, timezone_hours: int) -> list[requests.Response]:
        hours = int(timezone_hours)
        if not -12 <= hours <= 14:
            raise ValueError("Часовой пояс должен быть от UTC-12 до UTC+14.")
        time_zone = f"GMT{hours:+03d}:00"
        operations = (
            ("/LAPI/V1.0/System/Time", {"TimeZone": time_zone, "DeviceTime": int(time.time())}),
            ("/LAPI/V1.0/System/Time/NTP", {
                "Num": 1,
                "NTPServerInfos": [{"Enabled": 1, "AddressType": 2,
                    "DomainName": "ntp.goodline.info", "Port": 123,
                    "SynchronizeInterval": 3600}],
            }),
            ("/LAPI/V1.0/System/Time/SyncMode", {"Mode": 3}),
        )
        replies = []
        for path, payload in operations:
            replies.append(self.proxy_pipe(camera, self._raw_http_put(path, payload)))
        return replies

    def get_image_enhance(self, camera: AgentCamera) -> dict[str, Any]:
        return self._get_lapi_json(camera, self.IMAGE_ENHANCE_PATH)

    def set_image_rotation(self, camera: AgentCamera, current: dict[str, Any],
                           rotation: int) -> tuple[int, int]:
        value = int(rotation)
        if value not in (0, 1, 2, 3):
            raise ValueError("ImageRotation должен быть 0–3.")
        payload = json.loads(json.dumps(current))
        if "ImageRotation" not in payload:
            raise KeyError("Не получено текущее значение ImageRotation.")
        before = int(payload["ImageRotation"])
        payload["ImageRotation"] = value
        self.proxy_pipe(camera, self._raw_http_put(self.IMAGE_ENHANCE_PATH, payload))
        confirmed = self.get_image_enhance(camera)
        after = int(confirmed.get("ImageRotation", -1))
        if after != value:
            raise RuntimeError(f"Камера не подтвердила ориентацию {value} (получено {after}).")
        return before, after

    @staticmethod
    def _main_video_encode_info(payload: dict[str, Any]) -> dict[str, Any]:
        rows = payload.get("VideoStreamInfos")
        if not isinstance(rows, list):
            raise KeyError("Не найден VideoStreamInfos.")
        first = next((x for x in rows if isinstance(x, dict) and str(x.get("ID")) == "0"), None)
        if not first or not isinstance(first.get("VideoEncodeInfo"), dict):
            raise KeyError("Не найден основной поток ID=0 / VideoEncodeInfo.")
        return first["VideoEncodeInfo"]

    def get_video_streams(self, camera: AgentCamera) -> dict[str, Any]:
        return self._get_lapi_json(camera, self.VIDEO_STREAMS_PATH)

    def set_bitrate(self, camera: AgentCamera, current: dict[str, Any],
                    bitrate_kbps: int) -> tuple[str, str]:
        rate = int(bitrate_kbps)
        if not 1 <= rate <= 100000:
            raise ValueError("Битрейт должен быть от 1 до 100000 Кбит/с.")
        payload = json.loads(json.dumps(current))
        info = self._main_video_encode_info(payload)
        before = str(info.get("BitRate", ""))
        info["BitRate"] = str(rate)
        self.proxy_pipe(camera, self._raw_http_put(self.VIDEO_STREAMS_PATH, payload))
        confirmed = self.get_video_streams(camera)
        after = str(self._main_video_encode_info(confirmed).get("BitRate", ""))
        if after != str(rate):
            raise RuntimeError(f"Камера не подтвердила битрейт {rate} (получено {after}).")
        return before, after
